/**
 * Compact status chips for the dense adjuster surfaces, in the Linear idiom: small,
 * hairline-outlined, tinted labels in normal case (not shouty caps), with tabular figures
 * so columns of them line up. Semantic color is reserved for verdicts
 * (approve/deny/investigate) and genuine risk; lifecycle status and dispositions read as
 * calm neutrals so a queue never becomes a wall of color.
 */

import type { ComponentProps } from 'react';
import { Badge, Tooltip, TooltipContent, TooltipTrigger } from '@databricks/appkit-ui/react';
import { Copy, ShieldAlert } from 'lucide-react';
import { cn } from '@/lib/utils';
import { verdictLabel, dispositionLabel, titleCase } from '@/lib/format';
import type { Str } from '@/lib/types';

type Tone = 'approve' | 'deny' | 'investigate' | 'neutral' | 'attention' | 'risk';

const TONE: Record<Tone, string> = {
  approve: 'border-success/30 bg-success/12 text-success',
  deny: 'border-destructive/30 bg-destructive/12 text-destructive',
  investigate: 'border-warning/30 bg-warning/12 text-warning',
  attention: 'border-warning/30 bg-warning/12 text-warning',
  // Risk is the strongest attention tone — the "gold" high-risk flag.
  risk: 'border-warning/40 bg-warning/15 text-warning',
  neutral: 'border-border bg-secondary text-secondary-foreground',
};

function Chip({ tone, className, ...props }: { tone: Tone } & ComponentProps<typeof Badge>) {
  return (
    <Badge
      variant="outline"
      className={cn(
        'gap-1 rounded-[5px] px-1.5 py-0 text-xs font-medium leading-5 tracking-normal tabular-nums',
        TONE[tone],
        className
      )}
      {...props}
    />
  );
}

function verdictTone(v: Str | undefined): Tone {
  switch (v) {
    case 'APPROVE':
      return 'approve';
    case 'DENY':
      return 'deny';
    case 'PEND_INVESTIGATE':
    case 'PEND':
      return 'investigate';
    default:
      return 'neutral';
  }
}

export function VerdictChip({ verdict, className }: { verdict: Str; className?: string }) {
  return (
    <Chip tone={verdictTone(verdict)} className={className}>
      {verdictLabel(verdict)}
    </Chip>
  );
}

export function DispositionChip({ disposition, className }: { disposition: Str; className?: string }) {
  return (
    <Chip tone="neutral" className={className}>
      {dispositionLabel(disposition)}
    </Chip>
  );
}

export function DecisionStatusChip({ status, className }: { status: Str; className?: string }) {
  // RECOMMENDED = awaiting a human; FINAL = decided and closed.
  const tone: Tone = status === 'RECOMMENDED' ? 'attention' : 'neutral';
  return (
    <Chip tone={tone} className={className}>
      {status ? titleCase(status) : '—'}
    </Chip>
  );
}

/**
 * Risk flags shown on the queue and cockpit header. There is exactly ONE fraud indicator —
 * the HIGH-RISK chip — and it appears only when the fraud workstream's `high_risk` gold
 * column is strictly true, carrying the `risk_reason` string as a hover tooltip (e.g. "Fraud
 * cluster: 10 claims from 5 customers on heat HEAT-000000 (concentration 0.50)."). We do NOT
 * render a chip off `fraud_cluster_id`: that is a graph-component (heat) id assigned to
 * virtually every claim, so a chip gated on its mere presence tagged nearly the whole queue
 * as fraud regardless of risk. Lower-/zero-risk claims therefore get no alarming chip here;
 * the only other marker is the neutral Duplicate chip.
 */
export function RiskFlags({
  duplicateOf,
  highRisk,
  riskReason,
  className,
}: {
  duplicateOf?: Str;
  highRisk?: boolean;
  riskReason?: Str;
  className?: string;
}) {
  // Strict `=== true`: never render the fraud chip for a falsy-but-present value (e.g. a stray
  // string "false"), only for a real high_risk flag.
  const showHighRisk = highRisk === true;
  const hasAny = Boolean(duplicateOf) || showHighRisk;
  if (!hasAny) return null;
  return (
    <span className={cn('inline-flex flex-wrap items-center gap-1', className)}>
      {duplicateOf && (
        <Chip tone="attention">
          <Copy className="h-3 w-3" aria-hidden />
          Duplicate
        </Chip>
      )}
      {showHighRisk &&
        (riskReason ? (
          <Tooltip>
            <TooltipTrigger asChild>
              <span
                tabIndex={0}
                className="rounded-[5px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <Chip tone="risk" className="cursor-help">
                  <ShieldAlert className="h-3 w-3" aria-hidden />
                  High risk
                </Chip>
              </span>
            </TooltipTrigger>
            <TooltipContent className="max-w-xs text-pretty leading-relaxed">{riskReason}</TooltipContent>
          </Tooltip>
        ) : (
          <Chip tone="risk">
            <ShieldAlert className="h-3 w-3" aria-hidden />
            High risk
          </Chip>
        ))}
    </span>
  );
}
