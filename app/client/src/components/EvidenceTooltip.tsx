/**
 * An accessible "how is this computed?" affordance. The trigger is a real focusable
 * button (keyboard + screen-reader reachable), and the tooltip carries the plain-language
 * approach for a deterministic evidence source. Relies on a single <TooltipProvider> at
 * the app root.
 */

import { Tooltip, TooltipContent, TooltipTrigger } from '@databricks/appkit-ui/react';
import { Info } from 'lucide-react';
import { cn } from '@/lib/utils';

export function EvidenceInfo({ label, content, className }: { label: string; content: string; className?: string }) {
  return (
    <Tooltip>
      <TooltipTrigger
        type="button"
        aria-label={`How ${label} is calculated`}
        className={cn(
          'inline-flex h-4 w-4 items-center justify-center rounded-full text-muted-foreground/80 transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
          className
        )}
      >
        <Info className="h-3.5 w-3.5" aria-hidden />
      </TooltipTrigger>
      <TooltipContent className="max-w-xs text-pretty leading-relaxed">{content}</TooltipContent>
    </Tooltip>
  );
}
