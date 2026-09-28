/**
 * Business Leader Dashboard (Business User) — the governed analytics surface. It embeds
 * the existing AI/BI dashboard (the canonical KPI + chart view) and pairs it with a
 * conversational analytics panel backed by the gold Genie space (OBO — runs as the
 * signed-in user). Embedding also requires the deployed app's domain on the workspace
 * AI/BI embedding approved-domains allowlist; until that's in place (or in local dev,
 * where the workspace host isn't injected) the screen shows an open-in-Databricks
 * fallback instead of a blank frame.
 */

import { useEffect, useMemo, useState } from 'react';
import { Button } from '@databricks/appkit-ui/react';
import { BarChart3, ExternalLink, LayoutDashboard } from 'lucide-react';
import { getBusinessDashboardConfig, ApiError } from '@/lib/api';
import type { BusinessDashboardConfig } from '@/lib/types';
import { PageHeader } from '@/components/PageHeader';
import { EmptyState, ErrorState, InlineNotice, LoadingPanel } from '@/components/States';
import { GenieAssistant } from '@/components/GenieAssistant';

/**
 * How long to wait for the embedded dashboard's handshake before falling back. A working
 * AI/BI embed posts messages to the parent (auth/resize) as it initializes; a frame
 * blocked by X-Frame-Options / CSP frame-ancestors runs no script and posts nothing, and
 * its `onError` does not reliably fire — so "no message from the embed origin within this
 * window" is our signal to show the open-in-Databricks fallback rather than a blank frame.
 */
const EMBED_READY_TIMEOUT_MS = 8000;

function DashboardFallback({ embedUrl }: { embedUrl: string | null }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 rounded-lg border border-dashed border-border bg-card/40 p-8 text-center">
      <BarChart3 className="h-7 w-7 text-muted-foreground" aria-hidden />
      <div className="max-w-md space-y-1.5">
        <p className="text-sm font-semibold text-foreground">Embedded dashboard unavailable here</p>
        <p className="text-sm text-muted-foreground">
          The governed AI/BI dashboard renders inline once this app’s domain is added to the workspace AI/BI embedding
          approved-domains allowlist.
        </p>
      </div>
      {embedUrl && (
        <Button asChild variant="outline" size="sm" className="gap-1.5">
          <a href={embedUrl} target="_blank" rel="noopener noreferrer">
            <ExternalLink className="h-3.5 w-3.5" aria-hidden />
            Open in Databricks
          </a>
        </Button>
      )}
    </div>
  );
}

export function BusinessDashboardPage() {
  const [config, setConfig] = useState<BusinessDashboardConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [forbidden, setForbidden] = useState(false);
  const [embedReady, setEmbedReady] = useState(false);
  const [embedFailed, setEmbedFailed] = useState(false);
  const [reloadNonce, setReloadNonce] = useState(0);

  useEffect(() => {
    const ctrl = new AbortController();
    let live = true;
    void (async () => {
      setLoading(true);
      setError(null);
      setForbidden(false);
      setEmbedReady(false);
      setEmbedFailed(false);
      try {
        const cfg = await getBusinessDashboardConfig(ctrl.signal);
        if (live) setConfig(cfg);
      } catch (err: unknown) {
        if (!live || ctrl.signal.aborted) return;
        if (err instanceof ApiError && err.status === 403) setForbidden(true);
        else setError(err instanceof Error ? err.message : 'Failed to load the dashboard');
      } finally {
        if (live) setLoading(false);
      }
    })();
    return () => {
      live = false;
      ctrl.abort();
    };
  }, [reloadNonce]);

  const embedOrigin = useMemo(() => {
    if (!config?.embed_url) return null;
    try {
      return new URL(config.embed_url).origin;
    } catch {
      return null;
    }
  }, [config?.embed_url]);

  // Readiness watch: mark the embed ready on the first handshake message from its origin,
  // and fall back if none arrives before the timeout (see EMBED_READY_TIMEOUT_MS). Runs
  // only while we're actually attempting to embed.
  const attemptEmbed = Boolean(config?.embeddable && config?.embed_url);
  useEffect(() => {
    if (!attemptEmbed) return;
    const timer = window.setTimeout(() => setEmbedFailed(true), EMBED_READY_TIMEOUT_MS);
    function onMessage(e: MessageEvent) {
      if (embedOrigin && e.origin === embedOrigin) {
        setEmbedReady(true);
        window.clearTimeout(timer);
      }
    }
    window.addEventListener('message', onMessage);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener('message', onMessage);
    };
  }, [attemptEmbed, embedOrigin, reloadNonce]);

  const canEmbed = attemptEmbed && !embedFailed;

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title="Business Leader Dashboard"
        description="Governed claims analytics, with natural-language exploration."
        actions={
          config?.embed_url && (
            <Button asChild variant="outline" size="sm" className="gap-1.5">
              <a href={config.embed_url} target="_blank" rel="noopener noreferrer">
                <ExternalLink className="h-3.5 w-3.5" aria-hidden />
                Open in Databricks
              </a>
            </Button>
          )
        }
      />

      <div className="grid gap-4 lg:h-[calc(100vh-9rem)] lg:grid-cols-[minmax(0,1fr)_380px]">
        {/* Governed AI/BI dashboard */}
        <div className="flex min-h-0 flex-col">
          {loading ? (
            <div className="rounded-lg border border-border bg-card p-5">
              <LoadingPanel lines={10} />
            </div>
          ) : forbidden ? (
            <EmptyState
              icon={<LayoutDashboard className="h-6 w-6" />}
              title="Not available for your role"
              message="The business dashboard is a Business-User surface."
            />
          ) : error ? (
            <ErrorState message={error} onRetry={() => setReloadNonce((n) => n + 1)} />
          ) : canEmbed && config?.embed_url ? (
            <div className="flex min-h-[70vh] flex-1 flex-col overflow-hidden rounded-lg border border-border bg-card">
              <div className="flex items-center gap-2 border-b border-border px-3 py-2 text-sm font-semibold text-foreground">
                <BarChart3 className="h-4 w-4 text-muted-foreground" aria-hidden />
                Steel quality claims analytics
              </div>
              <div className="relative min-h-0 flex-1">
                <iframe
                  src={config.embed_url}
                  title="Steel quality claims analytics dashboard"
                  loading="lazy"
                  className="h-full w-full border-0"
                  onError={() => setEmbedFailed(true)}
                />
                {!embedReady && (
                  <div className="absolute inset-0 grid place-items-center bg-card p-6">
                    <LoadingPanel lines={8} className="w-full max-w-md" />
                  </div>
                )}
              </div>
            </div>
          ) : (
            <div className="flex min-h-[70vh] flex-1 flex-col">
              <DashboardFallback embedUrl={config?.embed_url ?? null} />
            </div>
          )}
        </div>

        {/* Conversational analytics (gold Genie space, OBO) */}
        <aside className="flex min-h-0 flex-col">
          {loading ? (
            <div className="rounded-lg border border-border bg-card p-5">
              <LoadingPanel lines={6} />
            </div>
          ) : forbidden ? (
            <InlineNotice>The analytics assistant is available to Business Users.</InlineNotice>
          ) : (
            <div className="flex h-[520px] flex-col overflow-hidden rounded-lg border border-border bg-card lg:h-full">
              <GenieAssistant
                alias={config?.genie_chat_alias ?? 'business'}
                title="Ask the analytics"
                description="Gold analytics Genie space — natural-language questions"
                placeholder="Ask about approvals, denials, overrides, trends…"
                className="h-full"
              />
            </div>
          )}
        </aside>
      </div>
    </div>
  );
}
