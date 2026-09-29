import { describe, it, expect, vi } from 'vitest';
import type { Request, Response } from 'express';
import { authorize, authorizeSet, actionForPath, makeAuthz, type Role, type RoleSet } from './authz';

/** A role set from a role list (empty list => empty set => hard deny). */
function set(...roles: Role[]): RoleSet {
  return new Set(roles);
}

describe('permission matrix', () => {
  it('adjuster may queue/cockpit/finalize/history/copilot, not business surfaces', () => {
    expect(authorize('adjuster', 'queue')).toBe(true);
    expect(authorize('adjuster', 'cockpit')).toBe(true);
    expect(authorize('adjuster', 'finalize')).toBe(true);
    expect(authorize('adjuster', 'claims_history')).toBe(true);
    expect(authorize('adjuster', 'cockpit_copilot')).toBe(true);
    expect(authorize('adjuster', 'history_chat')).toBe(true);
    expect(authorize('adjuster', 'business_dashboard')).toBe(false);
    expect(authorize('adjuster', 'business_chat')).toBe(false);
  });

  it('business_user may business dashboard/chat + history + history assistant, not adjuster surfaces', () => {
    expect(authorize('business_user', 'business_dashboard')).toBe(true);
    expect(authorize('business_user', 'business_chat')).toBe(true);
    expect(authorize('business_user', 'claims_history')).toBe(true);
    // The operational Claims-History assistant is allowed to both roles...
    expect(authorize('business_user', 'history_chat')).toBe(true);
    expect(authorize('business_user', 'queue')).toBe(false);
    expect(authorize('business_user', 'cockpit')).toBe(false);
    expect(authorize('business_user', 'finalize')).toBe(false);
    // ...but the adjuster-only cockpit copilot is NOT widened by that.
    expect(authorize('business_user', 'cockpit_copilot')).toBe(false);
  });

  it('both roles may read their own identity; a null role may not', () => {
    expect(authorize('adjuster', 'identity')).toBe(true);
    expect(authorize('business_user', 'identity')).toBe(true);
    expect(authorize(null, 'identity')).toBe(false);
  });

  it('a null role is denied everything', () => {
    expect(authorize(null, 'claims_history')).toBe(false);
    expect(authorize(null, 'queue')).toBe(false);
  });
});

describe('authorizeSet — union of the roles the caller actually holds', () => {
  it('a BOTH-roles caller is allowed BOTH surfaces (adjuster AND business actions)', () => {
    const both = set('adjuster', 'business_user');
    // adjuster-only actions
    expect(authorizeSet(both, 'queue')).toBe(true);
    expect(authorizeSet(both, 'cockpit')).toBe(true);
    expect(authorizeSet(both, 'finalize')).toBe(true);
    expect(authorizeSet(both, 'cockpit_copilot')).toBe(true);
    // business-only actions
    expect(authorizeSet(both, 'business_dashboard')).toBe(true);
    expect(authorizeSet(both, 'business_chat')).toBe(true);
    // shared
    expect(authorizeSet(both, 'claims_history')).toBe(true);
  });

  it('a BUSINESS-ONLY caller is still denied every adjuster-only action', () => {
    const bizOnly = set('business_user');
    expect(authorizeSet(bizOnly, 'queue')).toBe(false);
    expect(authorizeSet(bizOnly, 'cockpit')).toBe(false);
    expect(authorizeSet(bizOnly, 'finalize')).toBe(false);
    expect(authorizeSet(bizOnly, 'cockpit_copilot')).toBe(false);
    // ...but its own surfaces are allowed.
    expect(authorizeSet(bizOnly, 'business_dashboard')).toBe(true);
    expect(authorizeSet(bizOnly, 'business_chat')).toBe(true);
  });

  it('an ADJUSTER-ONLY caller is still denied the business surfaces', () => {
    const adjOnly = set('adjuster');
    expect(authorizeSet(adjOnly, 'business_dashboard')).toBe(false);
    expect(authorizeSet(adjOnly, 'business_chat')).toBe(false);
    expect(authorizeSet(adjOnly, 'finalize')).toBe(true);
  });

  it('an empty / null set is denied everything', () => {
    expect(authorizeSet(set(), 'claims_history')).toBe(false);
    expect(authorizeSet(set(), 'queue')).toBe(false);
    expect(authorizeSet(null, 'identity')).toBe(false);
    expect(authorizeSet(undefined, 'business_dashboard')).toBe(false);
  });
});

describe('actionForPath', () => {
  it('maps each guarded surface', () => {
    expect(actionForPath('GET', '/api/whoami')).toBe('identity');
    expect(actionForPath('GET', '/api/queue')).toBe('queue');
    expect(actionForPath('GET', '/api/claims/CLM-1')).toBe('cockpit');
    expect(actionForPath('POST', '/api/claims/CLM-1/finalize')).toBe('finalize');
    expect(actionForPath('GET', '/api/history')).toBe('claims_history');
    expect(actionForPath('POST', '/api/genie/cockpit/messages')).toBe('cockpit_copilot');
    expect(actionForPath('POST', '/api/genie/history/messages')).toBe('history_chat');
    expect(actionForPath('POST', '/api/genie/business/messages')).toBe('business_chat');
    expect(actionForPath('GET', '/api/business/dashboard')).toBe('business_dashboard');
    expect(actionForPath('GET', '/api/analytics/query')).toBe('business_dashboard');
  });

  it('maps the source drill-through to cockpit (adjuster-only, same as the cockpit detail)', () => {
    expect(actionForPath('GET', '/api/source/spec_clauses/A653%2FNA%2FE%2Fmechanical')).toBe('cockpit');
    expect(actionForPath('GET', '/api/source/prior_claims/CLM-1')).toBe('cockpit');
    expect(actionForPath('GET', '/api/source/customer_heat_risk/CU-1')).toBe('cockpit');
    // Only GET is a drill-through; a write verb is not this surface.
    expect(actionForPath('POST', '/api/source/prior_claims/CLM-1')).toBeNull();
  });

  it('finalize requires POST; GET on the claim is cockpit', () => {
    expect(actionForPath('GET', '/api/claims/CLM-1/finalize')).toBeNull();
    expect(actionForPath('GET', '/api/claims/CLM-1')).toBe('cockpit');
  });

  it('returns null for unguarded paths', () => {
    expect(actionForPath('GET', '/health')).toBeNull();
    expect(actionForPath('GET', '/assets/app.js')).toBeNull();
  });
});

// Minimal response/request mocks capturing only what the middleware touches. Single
// `as` assertions to the express types are allowed (the ast-grep rule bans only the
// `as unknown as` double assertion); each mock is structurally a supertype target.
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

function mkReq(method: string, path: string, headers: Record<string, string> = {}): Request {
  const req: Pick<Request, 'method' | 'path' | 'headers'> = { method, path, headers };
  return req as Request;
}

describe('makeAuthz middleware', () => {
  /** A resolver returning a fixed server-resolved role set (empty => hard deny). */
  const resolver =
    (...roles: Role[]) =>
    () =>
      set(...roles);

  it('denies a business user the finalize endpoint (403)', async () => {
    const mw = makeAuthz(resolver('business_user'));
    const res = mkRes();
    const next = vi.fn();
    await mw(mkReq('POST', '/api/claims/CLM-1/finalize'), res, next);
    expect(next).not.toHaveBeenCalled();
    expect(res.statusCode).toBe(403);
  });

  it('denies a business user the queue endpoint (403)', async () => {
    const mw = makeAuthz(resolver('business_user'));
    const res = mkRes();
    const next = vi.fn();
    await mw(mkReq('GET', '/api/queue'), res, next);
    expect(res.statusCode).toBe(403);
  });

  it('allows an adjuster the source drill-through, denies a business user (adjuster-only)', async () => {
    const adj = mkRes();
    const adjNext = vi.fn();
    await makeAuthz(resolver('adjuster'))(mkReq('GET', '/api/source/prior_claims/CLM-1'), adj, adjNext);
    expect(adjNext).toHaveBeenCalledOnce();
    expect(adj.statusCode).toBeUndefined();

    const biz = mkRes();
    const bizNext = vi.fn();
    await makeAuthz(resolver('business_user'))(mkReq('GET', '/api/source/prior_claims/CLM-1'), biz, bizNext);
    expect(bizNext).not.toHaveBeenCalled();
    expect(biz.statusCode).toBe(403);
  });

  it('denies an adjuster the business dashboard data endpoint (403)', async () => {
    const mw = makeAuthz(resolver('adjuster'));
    const res = mkRes();
    const next = vi.fn();
    await mw(mkReq('GET', '/api/analytics/query'), res, next);
    expect(res.statusCode).toBe(403);
  });

  it('allows an adjuster to finalize (next)', async () => {
    const mw = makeAuthz(resolver('adjuster'));
    const res = mkRes();
    const next = vi.fn();
    await mw(mkReq('POST', '/api/claims/CLM-1/finalize'), res, next);
    expect(next).toHaveBeenCalledOnce();
    expect(res.statusCode).toBeUndefined();
  });

  it('allows both roles claims history', async () => {
    for (const role of ['adjuster', 'business_user'] as Role[]) {
      const res = mkRes();
      const next = vi.fn();
      await makeAuthz(resolver(role))(mkReq('GET', '/api/history'), res, next);
      expect(next).toHaveBeenCalledOnce();
    }
  });

  it('allows both roles the operational Claims-History assistant (/api/genie/history)', async () => {
    for (const role of ['adjuster', 'business_user'] as Role[]) {
      const res = mkRes();
      const next = vi.fn();
      await makeAuthz(resolver(role))(mkReq('POST', '/api/genie/history/messages'), res, next);
      expect(next).toHaveBeenCalledOnce();
      expect(res.statusCode).toBeUndefined();
    }
  });

  it('still denies a business user the adjuster-only cockpit copilot (/api/genie/cockpit)', async () => {
    const res = mkRes();
    const next = vi.fn();
    await makeAuthz(resolver('business_user'))(mkReq('POST', '/api/genie/cockpit/messages'), res, next);
    expect(next).not.toHaveBeenCalled();
    expect(res.statusCode).toBe(403);
  });

  it('allows both roles to read their own identity (/api/whoami)', async () => {
    for (const role of ['adjuster', 'business_user'] as Role[]) {
      const res = mkRes();
      const next = vi.fn();
      await makeAuthz(resolver(role))(mkReq('GET', '/api/whoami'), res, next);
      expect(next).toHaveBeenCalledOnce();
      expect(res.statusCode).toBeUndefined();
    }
  });

  it('denies /api/whoami to a no-role caller (403)', async () => {
    const res = mkRes();
    const next = vi.fn();
    await makeAuthz(resolver())(mkReq('GET', '/api/whoami'), res, next);
    expect(next).not.toHaveBeenCalled();
    expect(res.statusCode).toBe(403);
  });

  it('a no-role caller is denied a guarded route', async () => {
    const res = mkRes();
    const next = vi.fn();
    await makeAuthz(resolver())(mkReq('GET', '/api/queue'), res, next);
    expect(res.statusCode).toBe(403);
  });

  it('a resolver failure is a hard deny, never an open door', async () => {
    const mw = makeAuthz(() => {
      throw new Error('scim down');
    });
    const res = mkRes();
    const next = vi.fn();
    await mw(mkReq('GET', '/api/queue'), res, next);
    expect(next).not.toHaveBeenCalled();
    expect(res.statusCode).toBe(403);
  });

  it('passes through non-API paths without resolving a role', async () => {
    const resolve = vi.fn(() => set());
    const res = mkRes();
    const next = vi.fn();
    await makeAuthz(resolve)(mkReq('GET', '/health'), res, next);
    expect(next).toHaveBeenCalledOnce();
    expect(resolve).not.toHaveBeenCalled();
  });

  it('default-DENIES an unmapped /api/* route (fail closed), without resolving a role', async () => {
    const resolve = vi.fn(() => set('adjuster'));
    const res = mkRes();
    const next = vi.fn();
    await makeAuthz(resolve)(mkReq('GET', '/api/some-new-endpoint'), res, next);
    expect(next).not.toHaveBeenCalled();
    expect(res.statusCode).toBe(403);
    expect(resolve).not.toHaveBeenCalled();
  });

  it('stashes the resolved role set on res.locals for downstream /whoami', async () => {
    const mw = makeAuthz(resolver('adjuster', 'business_user'));
    const res = mkRes();
    const next = vi.fn();
    await mw(mkReq('GET', '/api/whoami'), res, next);
    expect(next).toHaveBeenCalledOnce();
    expect((res.locals as { roles?: Role[] }).roles).toEqual(['adjuster', 'business_user']);
  });

  // --- The persona switch is a VIEW preference, not a privilege escalation ----------
  describe('the switch cannot escalate: authz ignores any client-asserted active role', () => {
    it('allows a BOTH-roles caller both the adjuster AND the business surfaces', async () => {
      const mw = makeAuthz(resolver('adjuster', 'business_user'));
      for (const [method, path] of [
        ['GET', '/api/queue'],
        ['POST', '/api/claims/CLM-1/finalize'],
        ['GET', '/api/claims/CLM-1'],
        ['GET', '/api/analytics/query'],
        ['GET', '/api/business/dashboard'],
        ['POST', '/api/genie/cockpit/messages'],
        ['POST', '/api/genie/business/messages'],
      ] as const) {
        const res = mkRes();
        const next = vi.fn();
        await mw(mkReq(method, path), res, next);
        expect(next, `${method} ${path} should be allowed for a both-roles caller`).toHaveBeenCalledOnce();
        expect(res.statusCode).toBeUndefined();
      }
    });

    it('403s a BUSINESS-ONLY caller on cockpit/finalize/queue EVEN WITH x-active-role: adjuster', async () => {
      // The client asks to act as an adjuster; the server resolved only business_user,
      // so the adjuster-only surfaces must stay closed. The middleware never reads the
      // header — it authorizes strictly against the resolved set.
      const mw = makeAuthz(resolver('business_user'));
      for (const [method, path] of [
        ['POST', '/api/claims/CLM-1/finalize'],
        ['GET', '/api/queue'],
        ['GET', '/api/claims/CLM-1'],
        ['POST', '/api/genie/cockpit/messages'],
      ] as const) {
        const res = mkRes();
        const next = vi.fn();
        await mw(mkReq(method, path, { 'x-active-role': 'adjuster' }), res, next);
        expect(next, `${method} ${path} must stay denied for a business-only caller`).not.toHaveBeenCalled();
        expect(res.statusCode).toBe(403);
      }
    });

    it('403s an ADJUSTER-ONLY caller on the business surfaces EVEN WITH x-active-role: business_user', async () => {
      const mw = makeAuthz(resolver('adjuster'));
      for (const [method, path] of [
        ['GET', '/api/business/dashboard'],
        ['GET', '/api/analytics/query'],
        ['POST', '/api/genie/business/messages'],
      ] as const) {
        const res = mkRes();
        const next = vi.fn();
        await mw(mkReq(method, path, { 'x-active-role': 'business_user' }), res, next);
        expect(next, `${method} ${path} must stay denied for an adjuster-only caller`).not.toHaveBeenCalled();
        expect(res.statusCode).toBe(403);
      }
    });
  });
});
