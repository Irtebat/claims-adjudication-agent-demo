/**
 * Compact status chips for the dense adjuster surfaces. Semantic color is reserved for
 * verdicts (approve/deny/investigate); lifecycle status and dispositions read as calm
 * neutrals so the queue doesn't turn into a wall of color. Chips are small, uppercase,
 * and use tabular caps so columns of them line up.
 */

import type { ComponentProps } from 'react';
import { Badge } from '@databricks/appkit-ui/react';
import { AlertTriangle, Copy, ShieldAlert } from 'lucide-react';
import { cn } from '@/lib/utils';
import { verdictLabel, dispositionLabel, titleCase } from '@/lib/format';
import type { Str } from '@/lib/types';

type Tone = 'approve' | 'deny' | 'investigate' | 'neutral' | 'attention';

const TONE: Record<Tone, string> = {
  approve: 'bg-success/10 text-success border-success/30',
  deny: 'bg-destructive/10 text-destructive border-destructive/30',
  investigate: 'bg-warning/12 text-warning border-warning/30',
  attention: 'bg-warning/12 text-warning border-warning/30',
  neutral: 'bg-secondary text-secondary-foreground border-border',
};

function Chip({ tone, className, ...props }: { tone: Tone } & ComponentProps<typeof Badge>) {
  return (
    <Badge
      variant="outline"
      className={cn(
        'gap-1 rounded-sm px-1.5 py-0 text-[0.68rem] font-semibold uppercase tracking-wide tabular-nums',
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

/** Small risk flags shown on the queue and cockpit (duplicate / heat / fraud). */
export function RiskFlags({
  duplicateOf,
  fraudCluster,
  heatRisk,
  className,
}: {
  duplicateOf?: Str;
  fraudCluster?: Str;
  heatRisk?: boolean;
  className?: string;
}) {
  const flags: { key: string; label: string; icon: typeof Copy }[] = [];
  if (duplicateOf) flags.push({ key: 'dup', label: 'Duplicate', icon: Copy });
  if (fraudCluster) flags.push({ key: 'fraud', label: 'Fraud cluster', icon: ShieldAlert });
  if (heatRisk) flags.push({ key: 'heat', label: 'Heat risk', icon: AlertTriangle });
  if (flags.length === 0) return null;
  return (
    <span className={cn('inline-flex flex-wrap items-center gap-1', className)}>
      {flags.map(({ key, label, icon: Icon }) => (
        <Chip key={key} tone="attention">
          <Icon className="h-3 w-3" aria-hidden />
          {label}
        </Chip>
      ))}
    </span>
  );
}
