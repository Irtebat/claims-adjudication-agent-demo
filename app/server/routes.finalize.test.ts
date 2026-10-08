/**
 * Finalize ROUTE wiring tests (regression lock for the live decision-submit bug).
 *
 * The live symptom: clicking any decision in the deployed cockpit returned
 * `POST /api/claims/:id/finalize -> 404 not_found` every time, so nothing persisted. The
 * route passed the URL `:id` (the CLAIM id) into runFinalize, which keys its row-locked
 * transaction on `adjudication_id` (`WHERE adjudication_id = $1`). Since claim_id !=
 * adjudication_id, the SELECT found no row -> not_found -> 404.
 *
 * These tests pin the contract: the finalize route binds the transaction to the body's
 * `adjudication_id` (never the path claim_id), and rejects a body missing it. They drive
 * the real `registerRoutes` handler with a fake Express app + fake Lakebase pool — no DB,
 * no network — asserting the FOR UPDATE select is bound to the adjudication id.
 *
 * Mock typing follows server/authz.test.ts: single `as` assertions to the express types
 * only (the ast-grep rule bans the `as unknown as` double assertion); each mock is a
 * structural subset of its target.
 */

import { describe, it, expect } from 'vitest';
import type { Application, Request, Response } from 'express';
import { registerRoutes, type CockpitAppKit } from './routes';
import type { QueryClient, Pool } from './finalize';

/** A fake pg client that records every query; the finalize SELECT returns zero rows. */
class RecordingClient implements QueryClient {
  calls: { text: string; params?: unknown[] }[] = [];
  released = false;
  query(text: string, params?: unknown[]) {
    this.calls.push({ text, params });
    return Promise.resolve({ rows: [] as Record<string, unknown>[], rowCount: 0 });
  }
  release() {
    this.released = true;
  }
  forUpdateCall() {
    return this.calls.find((c) => /FOR UPDATE/.test(c.text));
  }
}
class RecordingPool implements Pool {
  constructor(public client: RecordingClient) {}
  connect() {
    return Promise.resolve(this.client);
  }
}

/** Minimal express app capturing only the route registration the test drives. */
type RouteHandler = (req: Request, res: Response) => void | Promise<void>;
interface AppMock {
  get(path: string, ...handlers: RouteHandler[]): void;
  post(path: string, ...handlers: RouteHandler[]): void;
}

/** Capture the POST /api/claims/:id/finalize handler registered by registerRoutes. */
function captureFinalizeHandler(pool: Pool): RouteHandler {
  let handler: RouteHandler | null = null;
  const app: AppMock = {
    get() {},
    post(path: string, ...handlers: RouteHandler[]) {
      if (/\/api\/claims\/:id\/finalize$/.test(path)) handler = handlers[handlers.length - 1];
    },
  };
  const appkit: CockpitAppKit = {
    lakebase: {
      query: () => Promise.resolve({ rows: [] as Record<string, unknown>[] }),
      pool,
    },
    server: { extend: (fn: (a: Application) => void) => fn(app as Application) },
  };
  registerRoutes(appkit);
  if (!handler) throw new Error('finalize handler was not registered');
  return handler;
}

/** A minimal fake Response capturing status + json body (single `as` to Response). */
interface ResMock {
  statusCode?: number;
  body?: unknown;
  locals: Record<string, unknown>;
  status(c: number): ResMock;
  json(b: unknown): ResMock;
}
function mkRes(): ResMock & Response {
  const res: ResMock = {
    locals: {},
    status(c: number) {
      this.statusCode = c;
      return this;
    },
    json(b: unknown) {
      this.body = b;
      return this;
    },
  };
  return res as ResMock & Response;
}

function finalizeReq(body: Record<string, unknown>): Request {
  const req: Pick<Request, 'method' | 'path' | 'params' | 'headers' | 'body'> = {
    method: 'POST',
    path: '/api/claims/CLM-1/finalize',
    params: { id: 'CLM-1' },
    headers: { 'x-forwarded-email': 'adjuster@example.com' },
    body,
  };
  return req as Request;
}

const validBody = {
  adjudication_id: 'ADJ-9',
  final_verdict: 'APPROVE',
  final_disposition: 'CREDIT',
  approved_amount: 5000,
  override_reason: null,
};

describe('POST /api/claims/:id/finalize wiring', () => {
  it('scopes the finalize transaction to BOTH the body adjudication_id and the path claim_id', async () => {
    const client = new RecordingClient();
    const handler = captureFinalizeHandler(new RecordingPool(client));
    const res = mkRes();

    await handler(finalizeReq({ ...validBody }), res);

    // The row-locked SELECT must be scoped to BOTH the body adjudication_id AND the path
    // claim_id — [adjudication_id, claim_id]. The adjudication_id comes from the request
    // body (the old 404 bug passed the path claim_id here); the claim_id comes from the URL
    // path and prevents finalizing an adjudication that belongs to a different claim.
    const sel = client.forUpdateCall();
    expect(sel, 'the FOR UPDATE select should have run').toBeTruthy();
    expect(sel?.params).toEqual(['ADJ-9', 'CLM-1']);

    // With no matching adjudication row, finalize is a 404 not_found (not a 200/500).
    expect(res.statusCode).toBe(404);
  });

  it('rejects a finalize body missing adjudication_id with 400 (never reaches the pool)', async () => {
    const client = new RecordingClient();
    const handler = captureFinalizeHandler(new RecordingPool(client));
    const res = mkRes();

    const { adjudication_id: _omit, ...noAdjId } = validBody;
    await handler(finalizeReq(noAdjId), res);

    expect(res.statusCode).toBe(400);
    expect((res.body as { error?: string }).error).toBe('invalid_body');
    expect(client.calls, 'no transaction should start on a bad body').toHaveLength(0);
  });
});
