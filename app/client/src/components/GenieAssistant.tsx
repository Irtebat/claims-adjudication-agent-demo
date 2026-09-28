/**
 * A Genie natural-language panel with the trust affordances a governed AI surface
 * needs. The <GenieChat> component (SSE streaming, status, generated-SQL and result
 * rendering) is wrapped with:
 *   - an execution-identity line — the query runs on-behalf-of the signed-in user
 *     (the app declares user_api_scopes: dashboards.genie), so results respect that
 *     user's Unity Catalog grants;
 *   - a persistent "AI-generated — verify" disclaimer.
 * Both spaces this app talks to are OBO: `cockpit` (operational) and `business` (gold).
 */

import { Badge, GenieChat } from '@databricks/appkit-ui/react';
import { Sparkles, UserRound } from 'lucide-react';
import { cn } from '@/lib/utils';
import { useWhoami } from './whoami';

export function GenieAssistant({
  alias,
  title,
  description,
  placeholder,
  className,
}: {
  alias: string;
  title: string;
  description?: string;
  placeholder?: string;
  className?: string;
}) {
  const { data } = useWhoami();
  const identity = data?.email ?? data?.user ?? null;

  return (
    <div className={cn('flex min-h-0 flex-col', className)}>
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-3 py-2">
        <div className="flex items-center gap-2 min-w-0">
          <Sparkles className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
          <div className="min-w-0">
            <p className="text-sm font-semibold text-foreground">{title}</p>
            {description && <p className="truncate text-xs text-muted-foreground">{description}</p>}
          </div>
        </div>
        {identity && (
          <Badge variant="outline" className="gap-1 font-normal">
            <UserRound className="h-3 w-3" aria-hidden />
            <span className="max-w-[16ch] truncate">{identity}</span>
          </Badge>
        )}
      </div>

      <p className="border-b border-border bg-muted/40 px-3 py-1.5 text-xs text-muted-foreground">
        Runs on your behalf (OBO) — answers reflect your Unity Catalog access. AI-generated; verify against the
        generated SQL and cited sources before acting.
      </p>

      <div className="min-h-0 flex-1">
        <GenieChat alias={alias} placeholder={placeholder} className="h-full" />
      </div>
    </div>
  );
}
