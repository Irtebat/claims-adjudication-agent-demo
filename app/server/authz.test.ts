import { describe, it, expect, vi } from 'vitest';
import type { Request, Response } from 'express';
import { authorize, actionForPath, makeAuthz, type Role } from './authz';

describe('permission matrix', () => {
  it('adjuster may queue/cockpit/finalize/history/copilot, not business surfaces', () => {
    expect(authorize('adjuster', 'queue')).toBe(true);
    expect(authorize('adjuster', 'cockpit')).toBe(true);
    expect(authorize('adjuster', 'finalize')).toBe(true);
    expect(authorize('adjuster', 'claims_history')).toBe(true);
    expect(authorize('adjuster', 'cockpit_copilot')).toBe(true);
    expect(authorize('adjuster', 'business_dashboard')).toBe(false);
    expect(authorize('adjuster', 'business_chat')).toBe(false);
  });

  it('business_user may business dashboard/chat + history, not adjuster surfaces', () => {
    expect(authorize('business_user', 'business_dashboard')).toBe(true);
    expect(authorize('business_user', 'business_chat')).toBe(true);
    expect(authorize('business_user', 'claims_history')).toBe(true);
    expect(authorize('business_user', 'queue')).toBe(false);
    expect(authorize('business_user', 'cockpit')).toBe(false);
    expect(authorize('business_user', 'finalize')).toBe(false);
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

describe('actionForPath', () => {
  it('maps each guarded surface', () => {
    expect(actionForPath('GET', '/api/whoami')).toBe('identity');
    expect(actionForPath('GET', '/api/queue')).toBe('queue');
    expect(actionForPath('GET', '/api/claims/CLM-1')).toBe('cockpit');
    expect(actionForPath('POST', '/api/claims/CLM-1/finalize')).toBe('finalize');
    expect(actionForPath('GET', '/api/history')).toBe('claims_history');
    expect(actionForPath('POST', '/api/genie/cockpit/messages')).toBe('cockpit_copilot');
    expect(actionForPath('POST', '/api/genie/business/messages')).toBe('business_chat');
    expect(actionForPath('GET', '/api/business/dashboard')).toBe('business_dashboard');
    expect(actionForPath('GET', '/api/analytics/query')).toBe('business_dashboard');
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
  status(c: number): ResMock;
  json(b: unknown): ResMock;
}

function mkRes(): ResMock & Response {
  const res: ResMock = {
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

function mkReq(method: string, path: string): Request {
  const req: Pick<Request, 'method' | 'path' | 'headers'> = { method, path, headers: {} };
  return req as Request;
}

describe('makeAuthz middleware', () => {
  const resolver = (role: Role | null) => () => role;

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
    await makeAuthz(resolver(null))(mkReq('GET', '/api/whoami'), res, next);
    expect(next).not.toHaveBeenCalled();
    expect(res.statusCode).toBe(403);
  });

  it('a no-role caller is denied a guarded route', async () => {
    const res = mkRes();
    const next = vi.fn();
    await makeAuthz(resolver(null))(mkReq('GET', '/api/queue'), res, next);
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
    const resolve = vi.fn(() => null);
    const res = mkRes();
    const next = vi.fn();
    await makeAuthz(resolve)(mkReq('GET', '/health'), res, next);
    expect(next).toHaveBeenCalledOnce();
    expect(resolve).not.toHaveBeenCalled();
  });

  it('default-DENIES an unmapped /api/* route (fail closed), without resolving a role', async () => {
    const resolve = vi.fn(() => 'adjuster' as Role);
    const res = mkRes();
    const next = vi.fn();
    await makeAuthz(resolve)(mkReq('GET', '/api/some-new-endpoint'), res, next);
    expect(next).not.toHaveBeenCalled();
    expect(res.statusCode).toBe(403);
    expect(resolve).not.toHaveBeenCalled();
  });
});
