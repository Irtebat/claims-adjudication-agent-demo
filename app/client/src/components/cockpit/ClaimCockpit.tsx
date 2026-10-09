/**
 * Claim Decision Cockpit — a near-full-screen modal opened from the Work Queue and from
 * Claims History. It shows the persisted agent recommendation, the deterministic
 * supporting sources, the cited clauses, and the coil/heat/customer context, alongside
 * the Claim Copilot. RECOMMENDED claims get the decision form; already-FINAL claims are
 * read-only and show who decided, when, and the diff from the recommendation (the
 * finalize endpoint is idempotent, so this is a faithful record, not a second action).
 */

import { useEffect, useState } from 'react';
import {
  Badge,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@databricks/appkit-ui/react';
import { CircleCheck, Clock, TriangleAlert, UserCheck } from 'lucide-react';
import { getClaim, ApiError } from '@/lib/api';
import { DASH, money, pct, shortDate, verdictLabel, dispositionLabel } from '@/lib/format';
import type { ClaimDetail, DecisionRecord, FinalizeResult, SourceTarget } from '@/lib/types';
import { DecisionStatusChip, RiskFlags, VerdictChip, DispositionChip } from '@/components/StatusChip';
import { MetaStat } from '@/components/PageHeader';
import { ErrorState, LoadingPanel } from '@/components/States';
import { SupportingSources, Citations, ContextPanel, SimilarClaims } from './evidence';
import { SourceDetailSheet } from './SourceDetailSheet';
import { CopilotPanel } from './CopilotPanel';
import { DecisionForm } from './DecisionForm';

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-2.5">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{title}</h3>
      {children}
    </section>
  );
}

/** The persisted agent recommendation — always shown, prominent. */
function RecommendationHeader({ detail }: { detail: ClaimDetail }) {
  const a = detail.adjudication;
  // The recommended amount is the ORIGINAL persisted decision record's amount, not the
  // adjudication's approved_amount — the latter is overwritten on an amount override and
  // would misrepresent what the agent recommended. The recommended verdict/disposition
  // are stored on the adjudication as the recommendation and are not overwritten.
  const baseRec = detail.decision_records[0] ?? null;
  const recommendedAmount = baseRec?.approved_amount ?? a.approved_amount;
  return (
    <div className="grid grid-cols-2 gap-x-6 gap-y-4 rounded-lg border border-border bg-card p-4 sm:grid-cols-4">
      <div className="col-span-2 flex flex-col gap-1.5 sm:col-span-1">
        <span className="text-[0.68rem] font-medium uppercase tracking-wide text-muted-foreground">
          Recommended verdict
        </span>
        <div className="flex items-center gap-2">
          <VerdictChip verdict={a.recommended_verdict} />
          <DispositionChip disposition={a.recommended_disposition} />
        </div>
      </div>
      <MetaStat label="Recommended amount" value={money(recommendedAmount)} />
      <MetaStat label="Claimed" value={money(a.claimed_amount)} />
      <MetaStat label="Confidence" value={pct(a.confidence)} />
    </div>
  );
}

function ClaimFacts({ detail }: { detail: ClaimDetail }) {
  const a = detail.adjudication;
  return (
    <div className="grid grid-cols-2 gap-x-6 gap-y-3 rounded-lg border border-border bg-card p-4 sm:grid-cols-3 lg:grid-cols-4">
      <MetaStat label="Claim type" value={a.claim_type ?? DASH} />
      <MetaStat label="Defect" value={a.defect_code ?? DASH} />
      <MetaStat label="Claim date" value={shortDate(a.claim_date)} />
      <MetaStat label="Installed" value={shortDate(a.install_date)} />
      <MetaStat label="Coil" value={<span className="font-mono">{a.coil_id ?? DASH}</span>} />
      <MetaStat label="Customer" value={<span className="font-mono">{a.customer_id ?? DASH}</span>} />
      <MetaStat label="Claimed tonnage" value={a.claimed_tonnage != null ? `${a.claimed_tonnage} t` : DASH} />
      <MetaStat label="Environment" value={a.environment ?? DASH} />
      {a.defect_narrative && (
        <div className="col-span-2 sm:col-span-3 lg:col-span-4">
          <span className="text-[0.68rem] font-medium uppercase tracking-wide text-muted-foreground">
            Defect narrative
          </span>
          <p className="mt-0.5 text-sm text-foreground">{a.defect_narrative}</p>
        </div>
      )}
    </div>
  );
}

/** A single rec → final change line for the diff. */
function DiffLine({ label, from, to, changed }: { label: string; from: string; to: string; changed: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-3 text-sm">
      <span className="text-muted-foreground">{label}</span>
      {changed ? (
        <span className="tabular-nums">
          <span className="text-muted-foreground line-through">{from}</span>
          <span className="mx-1.5 text-muted-foreground">→</span>
          <span className="font-semibold text-foreground">{to}</span>
        </span>
      ) : (
        <span className="font-medium text-foreground tabular-nums">{to}</span>
      )}
    </div>
  );
}

/** Read-only summary for an already-finalized claim: who / what / when + the diff. */
function FinalizedSummary({ detail }: { detail: ClaimDetail }) {
  const a = detail.adjudication;
  const records = detail.decision_records;
  const finalRec: DecisionRecord | null = records.length ? records[records.length - 1] : null;
  const baseRec: DecisionRecord | null = records.length ? records[0] : null;

  const recVerdict = a.recommended_verdict;
  const recDisposition = a.recommended_disposition;
  const recAmount = baseRec?.approved_amount ?? null;
  const finalVerdict = finalRec?.recommended_verdict ?? a.verdict;
  const finalDisposition = finalRec?.recommended_disposition ?? a.disposition;
  const finalAmount = a.approved_amount;

  const overridden = Boolean(a.override_flag);
  const verdictChanged = verdictLabel(finalVerdict) !== verdictLabel(recVerdict);
  const dispChanged = dispositionLabel(finalDisposition) !== dispositionLabel(recDisposition);
  const amountChanged = money(finalAmount) !== money(recAmount);

  return (
    <div className="space-y-3 p-4">
      <div
        className={`flex items-start gap-2 rounded-md border px-3 py-2 ${
          overridden ? 'border-warning/30 bg-warning/10 text-warning' : 'border-success/30 bg-success/10 text-success'
        }`}
      >
        {overridden ? (
          <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
        ) : (
          <CircleCheck className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
        )}
        <div>
          <p className="text-sm font-semibold">
            {overridden ? 'Finalized with override' : 'Finalized — accepted recommendation'}
          </p>
          <p className="text-xs opacity-90">
            This claim is closed. The finalize action is idempotent and read-only here.
          </p>
        </div>
      </div>

      <div className="space-y-2 rounded-md border border-border bg-card p-3">
        <DiffLine
          label="Verdict"
          from={verdictLabel(recVerdict)}
          to={verdictLabel(finalVerdict)}
          changed={verdictChanged}
        />
        <DiffLine
          label="Disposition"
          from={dispositionLabel(recDisposition)}
          to={dispositionLabel(finalDisposition)}
          changed={dispChanged}
        />
        <DiffLine label="Amount" from={money(recAmount)} to={money(finalAmount)} changed={amountChanged} />
      </div>

      <div className="space-y-1.5 text-sm">
        <div className="flex items-center gap-2 text-muted-foreground">
          <UserCheck className="h-4 w-4" aria-hidden />
          Decided by <span className="font-medium text-foreground">{a.decided_by ?? DASH}</span>
        </div>
        <div className="flex items-center gap-2 text-muted-foreground">
          <Clock className="h-4 w-4" aria-hidden />
          {shortDate(a.finalized_at)}
        </div>
      </div>

      {overridden && a.override_reason && (
        <div className="rounded-md border border-border bg-secondary/40 p-3">
          <p className="mb-1 text-[0.68rem] font-medium uppercase tracking-wide text-muted-foreground">
            Override reason
          </p>
          <p className="text-sm text-foreground">{a.override_reason}</p>
        </div>
      )}
    </div>
  );
}

export function ClaimCockpit({
  claimId,
  adjudicationId,
  onClose,
  onFinalized,
}: {
  claimId: string | null;
  /** The exact adjudication the caller selected (from the queue/history row). When set,
   * the cockpit opens THAT adjudication rather than resolving one from the claim. */
  adjudicationId?: string | null;
  onClose: () => void;
  onFinalized?: () => void;
}) {
  const [detail, setDetail] = useState<ClaimDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [reloadNonce, setReloadNonce] = useState(0);
  // The evidence source the adjuster is drilling into (null = panel closed). Cleared when
  // the open claim changes so a stale row can't linger across claims.
  const [sourceTarget, setSourceTarget] = useState<SourceTarget | null>(null);

  useEffect(() => {
    if (!claimId) return;
    const ctrl = new AbortController();
    let live = true;
    void (async () => {
      setLoading(true);
      setError(null);
      setNotFound(false);
      setSourceTarget(null);
      try {
        const d = await getClaim(claimId, adjudicationId, ctrl.signal);
        if (live) setDetail(d);
      } catch (err: unknown) {
        if (!live || ctrl.signal.aborted) return;
        if (err instanceof ApiError && err.status === 404) setNotFound(true);
        else setError(err instanceof Error ? err.message : 'Failed to load the claim');
      } finally {
        if (live) setLoading(false);
      }
    })();
    return () => {
      live = false;
      ctrl.abort();
    };
  }, [claimId, adjudicationId, reloadNonce]);

  // The loaded detail is only "current" when it matches the open claim — this both
  // prevents a stale flash when switching claims and avoids resetting state
  // synchronously inside the effect.
  const current = detail && detail.adjudication.claim_id === claimId ? detail : null;
  const a = current?.adjudication;
  const isFinal = a?.decision_status === 'FINAL';
  const latestRecord: DecisionRecord | null =
    current && current.decision_records.length ? current.decision_records[current.decision_records.length - 1] : null;
  // Fraud gating: show the high-risk chip ONLY when the gold `high_risk` column is strictly
  // true; its `risk_reason` string becomes the chip's hover tooltip. Both columns arrive via
  // the customer_heat_risk context path and are read defensively (they may be absent).
  const chr = current?.context.customer_heat_risk ?? null;
  const highRisk = chr?.high_risk === true;
  const riskReason = typeof chr?.risk_reason === 'string' ? chr.risk_reason : null;
  const showLoading = Boolean(claimId) && (loading || (!current && !error && !notFound));

  function handleFinalized(_result: FinalizeResult) {
    onFinalized?.();
    // Reload so the modal flips to the read-only, finalized view (who/what/when + diff).
    setReloadNonce((n) => n + 1);
  }

  return (
    <Dialog open={Boolean(claimId)} onOpenChange={(open) => !open && onClose()}>
      <DialogContent
        showCloseButton
        /* Near-full-screen: ~24px inset all round, up to a 1720px cap. The explicit
           sm:max-w override is required — AppKit's DialogContent ships sm:max-w-lg, which
           (as a separate responsive key) would otherwise cap the modal at 512px on desktop. */
        className="flex h-[calc(100vh-3rem)] w-[calc(100vw-3rem)] max-w-[1720px] flex-col gap-0 overflow-hidden rounded-xl border-border p-0 sm:max-w-[1720px]"
      >
        <DialogHeader className="shrink-0 space-y-0 border-b border-border px-5 py-3">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 pr-8">
            <DialogTitle className="font-mono text-base font-medium">{claimId}</DialogTitle>
            {a && (
              <>
                <Badge variant="outline" className="rounded-[5px] border-border font-normal text-muted-foreground">
                  {a.claim_type ?? 'Claim'}
                </Badge>
                <DecisionStatusChip status={a.decision_status} />
                <RiskFlags duplicateOf={a.duplicate_of_claim_id} highRisk={highRisk} riskReason={riskReason} />
              </>
            )}
          </div>
          <DialogDescription className="sr-only">
            Claim decision cockpit: recommendation, supporting evidence, context, and the decision.
          </DialogDescription>
        </DialogHeader>

        {showLoading && (
          <div className="flex-1 overflow-auto p-6">
            <LoadingPanel lines={10} />
          </div>
        )}

        {notFound && !showLoading && (
          <div className="flex-1 p-6">
            <ErrorState title="Claim not found" message={`No adjudication exists for ${claimId ?? 'this claim'}.`} />
          </div>
        )}

        {error && !showLoading && (
          <div className="flex-1 p-6">
            <ErrorState message={error} onRetry={() => setReloadNonce((n) => n + 1)} />
          </div>
        )}

        {current && a && !showLoading && !error && !notFound && (
          <>
            <div className="grid min-h-0 flex-1 lg:grid-cols-[minmax(0,1fr)_420px]">
              {/* Analysis (scrollable) */}
              <div className="min-w-0 space-y-6 overflow-auto p-5">
                <Section title="Agent recommendation">
                  <RecommendationHeader detail={current} />
                </Section>
                <Section title="Claim">
                  <ClaimFacts detail={current} />
                </Section>
                <Section title="Supporting sources">
                  <SupportingSources record={latestRecord} />
                </Section>
                <Section title="Citations">
                  <Citations record={latestRecord} claimType={a.claim_type} onOpenSource={setSourceTarget} />
                </Section>
                <Section title="Context">
                  <ContextPanel context={current.context} onOpenSource={setSourceTarget} />
                </Section>
                <Section title="Similar prior claims">
                  <SimilarClaims claims={current.prior_claims} onOpenSource={setSourceTarget} />
                </Section>
              </div>

              {/* Decision + Copilot rail: two independent scroll regions stacked in a flex
                column. The decision/summary sits on top, CAPPED at 55% of the rail so a tall
                decision form can't crowd out the chat — it scrolls within its cap — and the
                Copilot fills the remaining ≥45% with its own internal scroll. The max-h cap is
                what makes this region's overflow-auto actually engage: a plain shrink-0 region
                keeps its full content height, so with the chat below it the form pushed its own
                lower half and the chat past the modal edge, where DialogContent's overflow-hidden
                clipped them (parts cut off / unreachable, chat unable to scroll). */}
              <div className="flex min-h-0 flex-col border-t border-border lg:border-l lg:border-t-0">
                <div className="max-h-[55%] shrink-0 overflow-auto border-b border-border">
                  {isFinal ? (
                    <FinalizedSummary detail={current} />
                  ) : (
                    <div className="p-4">
                      <DecisionForm adjudication={a} onFinalized={handleFinalized} />
                    </div>
                  )}
                </div>
                <CopilotPanel claimId={a.claim_id} record={latestRecord} priorClaims={current.prior_claims} />
              </div>
            </div>
            {/* Source drill-through — the full underlying row for whatever evidence item the
              adjuster clicked, layered above the cockpit. Read-only. */}
            <SourceDetailSheet target={sourceTarget} onClose={() => setSourceTarget(null)} />
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
