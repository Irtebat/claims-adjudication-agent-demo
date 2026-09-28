/**
 * Backend JSON routes for the claims cockpit (Wave 7, Stage A — headless).
 *
 * Adjuster surfaces: queue, cockpit detail, finalize, claims history.
 * Business surface: business dashboard metadata (analytics data is served by the
 * analytics plugin's config/queries; business chat + cockpit copilot are the Genie
 * plugin's /api/genie/:alias routes). Every route is behind the global authz
 * middleware (see server.ts); the Genie/analytics plugin routes are guarded there too.
 *
 * All Lakebase access uses the App-SP pool (appkit.lakebase). OBO is used only for
 * the governed Genie surfaces (the plugin's own routes). Clean loading/empty/error
 * semantics: 200 with data (possibly empty arrays), 400 on bad input, 404 when a
 * claim/adjudication is absent, 500 on unexpected server error.
 */

import { z } from 'zod';
import type { Application, Request, Response } from 'express';
import { decidedBy, getUserIdentity } from './identity';
import type { Role } from './authz';

/**
 * The default active role for a caller's role set — the persona the client opens in.
 * Adjuster (the operational surface) wins for a dual-role caller; otherwise the single
 * role; null when the set is empty. This is a VIEW default only — the server authorizes
 * against the full set, never this value.
 */
function defaultRole(roles: Role[]): Role | null {
  if (roles.includes('adjuster')) return 'adjuster';
  if (roles.includes('business_user')) return 'business_user';
  return null;
}
import { runFinalize, type Pool } from './finalize';
import {
  queueSql,
  historySql,
  cockpitAdjudicationSql,
  cockpitDecisionRecordsSql,
  cockpitContextSql,
  cockpitPrecedentSql,
  type ListFilters,
} from './sql';

export interface CockpitAppKit {
  lakebase: {
    query(text: string, params?: unknown[]): Promise<{ rows: Record<string, unknown>[] }>;
    pool: Pool;
  };
  server: { extend(fn: (app: Application) => void): void };
}

const FinalizeBody = z.object({
  final_verdict: z.enum(['APPROVE', 'DENY', 'PEND_INVESTIGATE']),
  final_disposition: z.string().min(1),
  approved_amount: z.number().nonnegative(),
  override_reason: z.string().nullish(),
});

/** Safe stringify for unknown DB text columns (avoids "[object Object]"). */
function asText(v: unknown): string {
  if (typeof v === 'string') return v;
  if (typeof v === 'number' || typeof v === 'bigint') return v.toString();
  return '';
}

/** The gold AI/BI dashboard embedded on the business surface (overridable via env). */
const DASHBOARD_ID = process.env.DATABRICKS_DASHBOARD_ID ?? '01f1bac220111001a171872b5185e8e6';

/** Normalize the workspace host the Apps runtime injects to `https://<host>` (no slash). */
function workspaceHost(): string | null {
  const raw = (process.env.DATABRICKS_HOST ?? '').trim();
  if (!raw) return null;
  const withScheme = /^https?:\/\//i.test(raw) ? raw : `https://${raw}`;
  return withScheme.replace(/\/+$/, '');
}

/** The AI/BI dashboard embed URL, or null when the host is not yet known (local dev). */
function dashboardEmbedUrl(): string | null {
  const host = workspaceHost();
  return host ? `${host}/embed/dashboardsv3/${DASHBOARD_ID}` : null;
}

function listFilters(req: Request): ListFilters {
  const q = req.query;
  const str = (k: string): string | undefined => (typeof q[k] === 'string' ? q[k] : undefined);
  const num = (k: string): number | undefined => {
    const v = str(k);
    return v !== undefined && v !== '' ? Number(v) : undefined;
  };
  return {
    claimType: str('claim_type'),
    customerId: str('customer_id'),
    verdict: str('verdict'),
    sort: str('sort'),
    order: str('order'),
    limit: num('limit'),
    offset: num('offset'),
  };
}

export function registerRoutes(appkit: CockpitAppKit): void {
  const lb = appkit.lakebase;

  appkit.server.extend((app: Application) => {
    // (0) Identity echo — the signed-in user + the FULL role set the authz guard
    // resolved for them (stashed on res.locals) plus a default active role. The client
    // uses this only to shape navigation and seed the persona switch; every route below
    // independently re-enforces the permission matrix against the resolved set, so the
    // client's chosen active role is never trusted for authorization.
    app.get('/api/whoami', (req: Request, res: Response) => {
      const { email, user } = getUserIdentity(req);
      const roles = (res.locals as { roles?: Role[] }).roles ?? [];
      res.json({ email, user, roles, defaultRole: defaultRole(roles) });
    });

    // (a) Adjuster queue — RECOMMENDED adjudications joined to their claim.
    app.get('/api/queue', async (req: Request, res: Response) => {
      try {
        const { text, params } = queueSql(listFilters(req));
        const { rows } = await lb.query(text, params);
        res.json({ items: rows, count: rows.length });
      } catch (err) {
        console.error('[queue] failed:', (err as Error).message);
        res.status(500).json({ error: 'queue_failed' });
      }
    });

    // (d) Claims history — FINAL adjudications with decided_by / override metadata.
    app.get('/api/history', async (req: Request, res: Response) => {
      try {
        const { text, params } = historySql(listFilters(req));
        const { rows } = await lb.query(text, params);
        res.json({ items: rows, count: rows.length });
      } catch (err) {
        console.error('[history] failed:', (err as Error).message);
        res.status(500).json({ error: 'history_failed' });
      }
    });

    // (b) Cockpit detail — recommendation + full decision-record trail + citations
    // + context + prior-claim precedent, all from ALREADY-PERSISTED data (not Genie).
    app.get('/api/claims/:id', async (req: Request, res: Response) => {
      try {
        const claimId = String(req.params.id);
        const adjQ = cockpitAdjudicationSql(claimId);
        const adj = await lb.query(adjQ.text, adjQ.params);
        if (adj.rows.length === 0) {
          res.status(404).json({ error: 'claim_not_found' });
          return;
        }
        const adjudication = adj.rows[0];
        const adjudicationId = asText(adjudication.adjudication_id);

        const recQ = cockpitDecisionRecordsSql(adjudicationId);
        const records = (await lb.query(recQ.text, recQ.params)).rows;

        const coilId = asText(adjudication.coil_id);
        const customerId = asText(adjudication.customer_id);
        const ctxQ = cockpitContextSql(coilId, customerId);
        const context = (await lb.query(ctxQ.text, ctxQ.params)).rows[0] ?? {};

        // Precedent claim ids come from the latest record's persisted precedent.
        const latest = records[records.length - 1] ?? {};
        const precedent = Array.isArray(latest.precedent) ? (latest.precedent as { claim_id?: string }[]) : [];
        const precedentIds = precedent.map((p) => p.claim_id).filter((x): x is string => Boolean(x));
        let priorClaims: Record<string, unknown>[] = [];
        if (precedentIds.length > 0) {
          const preQ = cockpitPrecedentSql(precedentIds);
          priorClaims = (await lb.query(preQ.text, preQ.params)).rows;
        }

        res.json({
          adjudication,
          decision_records: records,
          context,
          prior_claims: priorClaims,
        });
      } catch (err) {
        console.error('[cockpit] failed:', (err as Error).message);
        res.status(500).json({ error: 'cockpit_failed' });
      }
    });

    // (c) Finalize — the human-finalization transaction (Task 3).
    app.post('/api/claims/:id/finalize', async (req: Request, res: Response) => {
      const parsed = FinalizeBody.safeParse(req.body);
      if (!parsed.success) {
        res.status(400).json({ error: 'invalid_body', issues: parsed.error.issues });
        return;
      }
      const who = decidedBy(req);
      if (!who) {
        res.status(401).json({ error: 'no_user_identity' });
        return;
      }
      try {
        const result = await runFinalize(lb.pool, String(req.params.id), {
          finalVerdict: parsed.data.final_verdict,
          finalDisposition: parsed.data.final_disposition,
          approvedAmount: parsed.data.approved_amount,
          overrideReason: parsed.data.override_reason ?? null,
          decidedBy: who,
        });
        switch (result.status) {
          case 'finalized':
            res.status(200).json(result);
            return;
          case 'already_final':
            res.status(200).json(result);
            return;
          case 'not_found':
            res.status(404).json(result);
            return;
          case 'invalid':
            res.status(400).json(result);
            return;
        }
      } catch (err) {
        console.error('[finalize] failed:', (err as Error).message);
        res.status(500).json({ error: 'finalize_failed' });
      }
    });

    // (f) Business dashboard — wiring for the Business-User surface (this endpoint is
    // mapped to the `business_dashboard` action, so Adjusters are denied it). It returns
    // the embed URL for the governed AI/BI dashboard and the Genie alias for the gold
    // analytics chat (OBO). `embeddable` is false until the workspace host is known
    // (local dev) OR when embedding must still be enabled workspace-side; the client
    // falls back to an open-in-Databricks link in that case.
    app.get('/api/business/dashboard', (_req: Request, res: Response) => {
      const embedUrl = dashboardEmbedUrl();
      res.json({
        genie_chat_alias: 'business',
        dashboard_id: DASHBOARD_ID,
        embed_url: embedUrl,
        embeddable: Boolean(embedUrl),
      });
    });
  });
}
