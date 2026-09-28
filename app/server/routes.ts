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
import { decidedBy } from './identity';
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

    // (f) Business dashboard — the tabular data is served by the analytics plugin's
    // config/queries (governed warehouse), and the chat by the Genie 'business'
    // alias; both are behind the authz guard. This endpoint surfaces the wiring so
    // the business dashboard has an explicit, role-guarded backend contract.
    app.get('/api/business/dashboard', (_req: Request, res: Response) => {
      res.json({
        analytics_query_keys: ['business_kpis'],
        genie_chat_alias: 'business',
      });
    });
  });
}
