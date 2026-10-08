/**
 * The adjuster's decision surface for a RECOMMENDED claim. Accept the recommendation as
 * is, or override the verdict + disposition and/or the amount, or send the claim to
 * investigation. Any change from the recommendation is an OVERRIDE and requires a
 * reason. Submitting calls the existing finalize endpoint (one transaction: FINAL row +
 * human-final decision-record version + outbox). Client validation mirrors
 * server/finalize.ts, but the server remains authoritative — its `invalid` errors are
 * surfaced inline.
 */

import { useMemo, useState } from 'react';
import {
  Alert,
  AlertDescription,
  Button,
  Input,
  Label,
  RadioGroup,
  RadioGroupItem,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Textarea,
} from '@databricks/appkit-ui/react';
import { AlertCircle, Check, Search } from 'lucide-react';
import { finalizeClaim, ApiError } from '@/lib/api';
import { dispositionsFor, dispositionLabel, money, toNum, uiVerdict, verdictLabel } from '@/lib/format';
import type { Adjudication, FinalizeResult, Verdict } from '@/lib/types';

const VERDICTS: Verdict[] = ['APPROVE', 'DENY', 'PEND_INVESTIGATE'];

const ERROR_COPY: Record<string, string> = {
  override_reason_required: 'A reason is required to override the recommendation.',
  approved_amount_exceeds_claimed: 'The approved amount can’t exceed the claimed amount.',
  deny_must_be_zero_amount: 'A denied claim settles at $0.',
  pend_must_be_zero_amount: 'A claim sent to investigation settles at $0.',
  invalid_approve_disposition: 'Choose a disposition valid for an approval.',
  invalid_deny_disposition: 'Choose a disposition valid for a denial.',
  invalid_pend_disposition: 'Investigation uses the Investigate disposition.',
  invalid_approved_amount: 'Enter a valid, non-negative amount.',
  invalid_verdict: 'Choose a valid verdict.',
  decided_by_required: 'We couldn’t confirm your identity — reload and try again.',
};

function firstErrorCopy(codes: string[]): string {
  for (const c of codes) if (ERROR_COPY[c]) return ERROR_COPY[c];
  return codes.length ? `Rejected: ${codes.join(', ')}` : 'The decision was rejected.';
}

export function DecisionForm({
  adjudication,
  onFinalized,
}: {
  adjudication: Adjudication;
  onFinalized: (result: FinalizeResult) => void;
}) {
  // The persisted recommendation is OPERATIONAL ('PEND' for an investigate hold); reconcile it
  // to the UI verdict vocabulary so DISPOSITIONS_FOR resolves (operational 'PEND' has no entry
  // and used to crash the validation useMemo below on `.includes`).
  const recVerdict = uiVerdict(adjudication.recommended_verdict);
  const recDisposition = adjudication.recommended_disposition ?? dispositionsFor(recVerdict)[0];
  const recAmount = toNum(adjudication.approved_amount) ?? 0;
  const claimedAmount = toNum(adjudication.claimed_amount) ?? 0;

  const [verdict, setVerdict] = useState<Verdict>(recVerdict);
  const [disposition, setDisposition] = useState<string>(recDisposition);
  const [amount, setAmount] = useState<string>(String(recAmount));
  const [reason, setReason] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const amountNum = toNum(amount) ?? 0;
  const amountLocked = verdict !== 'APPROVE'; // DENY / investigate settle at 0

  const differs = useMemo(
    () => verdict !== recVerdict || disposition !== recDisposition || Math.abs(amountNum - recAmount) > 0.005,
    [verdict, disposition, amountNum, recVerdict, recDisposition, recAmount]
  );

  const validation = useMemo(() => {
    const errs: string[] = [];
    const dispositions = dispositionsFor(verdict);
    if (!dispositions.includes(disposition)) errs.push('disposition');
    if (verdict === 'APPROVE') {
      if (amountNum < 0) errs.push('amount');
      if (amountNum > claimedAmount + 0.005) errs.push('amount_exceeds');
    }
    if (differs && reason.trim().length === 0) errs.push('reason');
    return errs;
  }, [verdict, disposition, amountNum, claimedAmount, differs, reason]);

  const canSubmit = validation.length === 0 && !submitting;

  function changeVerdict(next: Verdict) {
    setVerdict(next);
    // Snap disposition + amount to values consistent with the new verdict.
    const valid = dispositionsFor(next);
    if (!valid.includes(disposition)) setDisposition(next === recVerdict ? recDisposition : valid[0]);
    if (next !== 'APPROVE') setAmount('0');
    else if (verdict !== 'APPROVE') setAmount(String(recAmount));
  }

  function acceptRecommendation() {
    setVerdict(recVerdict);
    setDisposition(recDisposition);
    setAmount(String(recAmount));
    setReason('');
    void submit({ v: recVerdict, d: recDisposition, a: recAmount, r: null });
  }

  function sendToInvestigation() {
    setVerdict('PEND_INVESTIGATE');
    setDisposition('PEND_INVESTIGATE');
    setAmount('0');
  }

  async function submit(override?: { v: Verdict; d: string; a: number; r: string | null }) {
    setSubmitting(true);
    setErrorMsg(null);
    const body = override
      ? {
          final_verdict: override.v,
          final_disposition: override.d,
          approved_amount: override.a,
          override_reason: override.r,
        }
      : {
          final_verdict: verdict,
          final_disposition: disposition,
          approved_amount: amountNum,
          override_reason: differs ? reason.trim() : null,
        };
    try {
      const result = await finalizeClaim(adjudication.claim_id, adjudication.adjudication_id, body);
      // Only a real finalization (or an idempotent already-FINAL hit — e.g. a concurrent
      // finalizer) advances the UI to the read-only finalized view. `invalid` and
      // `not_found` are surfaced inline so the decision never silently no-ops: previously a
      // not_found was treated as success and the cockpit reloaded with nothing persisted,
      // which read as "the page refreshed and nothing happened".
      if (result.status === 'invalid') {
        setErrorMsg(firstErrorCopy(result.errors));
        return;
      }
      if (result.status === 'not_found') {
        setErrorMsg('We couldn’t find this claim’s adjudication to finalize. Reload and try again.');
        return;
      }
      onFinalized(result);
    } catch (err) {
      const msg =
        err instanceof ApiError && err.status === 403
          ? 'You’re not permitted to finalize this claim.'
          : 'Couldn’t submit the decision. Check your connection and try again.';
      setErrorMsg(msg);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault();
        if (canSubmit) void submit();
      }}
    >
      {/* Accept — the recommendation, unchanged. Primary path. */}
      <div className="rounded-md border border-border bg-secondary/40 p-3">
        <div className="mb-2 flex items-baseline justify-between gap-2">
          <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Recommendation</span>
          <span className="text-sm font-semibold tabular-nums">{money(recAmount)}</span>
        </div>
        <p className="mb-3 text-sm text-foreground">
          {verdictLabel(recVerdict)} · {dispositionLabel(recDisposition)}
        </p>
        <Button type="button" className="w-full gap-1.5" onClick={acceptRecommendation} disabled={submitting}>
          <Check className="h-4 w-4" aria-hidden />
          Accept recommendation
        </Button>
      </div>

      <div className="relative">
        <div className="absolute inset-0 flex items-center" aria-hidden>
          <span className="w-full border-t border-border" />
        </div>
        <span className="relative mx-auto block w-fit bg-card px-2 text-[0.68rem] font-medium uppercase tracking-wide text-muted-foreground">
          or override
        </span>
      </div>

      <fieldset className="space-y-3" disabled={submitting}>
        <div className="space-y-1.5">
          <Label className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Verdict</Label>
          <RadioGroup
            className="grid grid-cols-3 gap-1.5"
            value={verdict}
            onValueChange={(v) => changeVerdict(v as Verdict)}
          >
            {VERDICTS.map((v) => (
              <Label
                key={v}
                htmlFor={`verdict-${v}`}
                className={`flex cursor-pointer items-center justify-center gap-1.5 rounded-md border px-2 py-2 text-sm font-medium transition-colors ${
                  verdict === v
                    ? 'border-primary bg-primary text-primary-foreground'
                    : 'border-border hover:bg-secondary'
                }`}
              >
                <RadioGroupItem id={`verdict-${v}`} value={v} className="sr-only" />
                {verdictLabel(v)}
              </Label>
            ))}
          </RadioGroup>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-1.5">
            <Label htmlFor="disposition" className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Disposition
            </Label>
            <Select value={disposition} onValueChange={setDisposition}>
              <SelectTrigger id="disposition">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {dispositionsFor(verdict).map((d) => (
                  <SelectItem key={d} value={d}>
                    {dispositionLabel(d)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="amount" className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Approved amount
            </Label>
            <Input
              id="amount"
              type="number"
              inputMode="decimal"
              min={0}
              max={claimedAmount}
              step={1}
              value={amount}
              disabled={amountLocked}
              onChange={(e) => setAmount(e.target.value)}
              className="tabular-nums"
            />
            {verdict === 'APPROVE' && <p className="text-xs text-muted-foreground">Claimed {money(claimedAmount)}</p>}
          </div>
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="reason" className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Override reason {differs && <span className="text-destructive">(required)</span>}
          </Label>
          <Textarea
            id="reason"
            rows={3}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder={
              differs
                ? 'Explain why you’re changing the recommendation…'
                : 'Not required when accepting the recommendation'
            }
            aria-invalid={validation.includes('reason')}
          />
        </div>
      </fieldset>

      {errorMsg && (
        <Alert variant="destructive">
          <AlertCircle className="h-4 w-4" aria-hidden />
          <AlertDescription>{errorMsg}</AlertDescription>
        </Alert>
      )}

      <div className="flex flex-col gap-2">
        <Button type="submit" variant={differs ? 'default' : 'outline'} disabled={!canSubmit}>
          {submitting ? 'Submitting…' : differs ? 'Submit override' : 'Submit decision'}
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="gap-1.5 text-muted-foreground"
          onClick={sendToInvestigation}
          disabled={submitting}
        >
          <Search className="h-3.5 w-3.5" aria-hidden />
          Send to investigation
        </Button>
      </div>
    </form>
  );
}
