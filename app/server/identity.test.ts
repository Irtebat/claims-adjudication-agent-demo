import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import type { Request } from 'express';
import { getUserIdentity, decidedBy, makeDatabricksRoleResolver, type ScimResponse, type ScimGroup } from './identity';

function reqWith(opts: { email?: string; token?: string } = {}): Request {
  const headers: Record<string, string> = {};
  if (opts.email) headers['x-forwarded-email'] = opts.email;
  if (opts.token) headers['x-forwarded-access-token'] = opts.token;
  const req: Pick<Request, 'headers'> = { headers };
  return req as Request;
}

/** A fetch stub that returns a SCIM /Me body carrying `groups`. */
function scimStub(groups: ScimGroup[], resp: { ok?: boolean; status?: number } = {}) {
  return vi.fn((_url: string, _init: { headers: Record<string, string>; signal?: AbortSignal }) =>
    Promise.resolve<ScimResponse>({
      ok: resp.ok ?? true,
      status: resp.status ?? 200,
      json: () => Promise.resolve({ groups }),
    })
  );
}

const HOST = 'https://ws.cloud.databricks.com';

describe('getUserIdentity / decidedBy', () => {
  it('extracts the OBO email from x-forwarded-email', () => {
    expect(getUserIdentity(reqWith({ email: 'a@x.com' })).email).toBe('a@x.com');
    expect(decidedBy(reqWith({ email: 'a@x.com' }))).toBe('a@x.com');
  });
  it('is null when the header is absent', () => {
    expect(getUserIdentity(reqWith()).email).toBeNull();
    expect(decidedBy(reqWith())).toBeNull();
  });
});

describe('makeDatabricksRoleResolver — per-user allowlist (override / escape hatch)', () => {
  const saved = { adj: process.env.ADJUSTER_USERS, biz: process.env.BUSINESS_USERS };
  beforeEach(() => {
    process.env.ADJUSTER_USERS = 'adj1@x.com, Adj2@x.com';
    process.env.BUSINESS_USERS = 'biz1@x.com';
    delete process.env.ADJUSTER_GROUPS;
    delete process.env.BUSINESS_GROUPS;
  });
  afterEach(() => {
    process.env.ADJUSTER_USERS = saved.adj;
    process.env.BUSINESS_USERS = saved.biz;
  });

  it('assigns adjuster / business_user from the allowlists (case-insensitive), no token or fetch needed', async () => {
    const fetchMock = scimStub([]);
    const resolve = makeDatabricksRoleResolver({ fetch: fetchMock, host: HOST });
    expect(await resolve(reqWith({ email: 'adj1@x.com' }))).toBe('adjuster');
    expect(await resolve(reqWith({ email: 'ADJ2@X.COM' }))).toBe('adjuster');
    expect(await resolve(reqWith({ email: 'biz1@x.com' }))).toBe('business_user');
    expect(fetchMock).not.toHaveBeenCalled(); // allowlist short-circuits before SCIM
  });

  it('hard-denies (null) an unlisted user with no token, and a missing identity', async () => {
    const fetchMock = scimStub([]);
    const resolve = makeDatabricksRoleResolver({ fetch: fetchMock, host: HOST });
    expect(await resolve(reqWith({ email: 'nobody@x.com' }))).toBeNull();
    expect(await resolve(reqWith())).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled(); // no token => never hits SCIM
  });
});

describe('makeDatabricksRoleResolver — group-based (PRIMARY, via SCIM /Me)', () => {
  const saved = {
    adjU: process.env.ADJUSTER_USERS,
    bizU: process.env.BUSINESS_USERS,
    adjG: process.env.ADJUSTER_GROUPS,
    bizG: process.env.BUSINESS_GROUPS,
    scope: process.env.GROUP_SCOPE,
  };
  beforeEach(() => {
    process.env.ADJUSTER_USERS = 'boss@x.com'; // for the override-beats-group test
    process.env.BUSINESS_USERS = '';
    process.env.ADJUSTER_GROUPS = 'steel-adjusters, 100-adj-id';
    process.env.BUSINESS_GROUPS = 'steel-business';
    delete process.env.GROUP_SCOPE;
  });
  afterEach(() => {
    process.env.ADJUSTER_USERS = saved.adjU;
    process.env.BUSINESS_USERS = saved.bizU;
    process.env.ADJUSTER_GROUPS = saved.adjG;
    process.env.BUSINESS_GROUPS = saved.bizG;
    process.env.GROUP_SCOPE = saved.scope;
  });

  it('maps a matching group -> adjuster (by display name)', async () => {
    const fetchMock = scimStub([{ display: 'Steel-Adjusters', value: 'g-1' }]);
    const resolve = makeDatabricksRoleResolver({ fetch: fetchMock, host: HOST });
    expect(await resolve(reqWith({ email: 'u@x.com', token: 'tok' }))).toBe('adjuster');
    expect(fetchMock).toHaveBeenCalledOnce();
    // default GROUP_SCOPE is `account`
    expect(fetchMock.mock.calls[0][0]).toBe(`${HOST}/api/2.0/account/scim/v2/Me`);
    expect(fetchMock.mock.calls[0][1].headers.Authorization).toBe('Bearer tok');
  });

  it('maps a matching group -> business_user (by display name)', async () => {
    const fetchMock = scimStub([{ display: 'steel-business', value: 'g-2' }]);
    const resolve = makeDatabricksRoleResolver({ fetch: fetchMock, host: HOST });
    expect(await resolve(reqWith({ email: 'u@x.com', token: 'tok' }))).toBe('business_user');
  });

  it('matches a group by id (value) as well as display', async () => {
    // display does not match any configured group; the group id (value) does.
    const fetchMock = scimStub([{ display: 'Some Unrelated Name', value: '100-adj-id' }]);
    const resolve = makeDatabricksRoleResolver({ fetch: fetchMock, host: HOST });
    expect(await resolve(reqWith({ email: 'u@x.com', token: 'tok' }))).toBe('adjuster');
  });

  it('hard-denies (null) when no group matches', async () => {
    const fetchMock = scimStub([{ display: 'random-team', value: 'g-9' }]);
    const resolve = makeDatabricksRoleResolver({ fetch: fetchMock, host: HOST });
    expect(await resolve(reqWith({ email: 'u@x.com', token: 'tok' }))).toBeNull();
  });

  it('hard-denies (null) when the OBO token is missing, without calling SCIM', async () => {
    const fetchMock = scimStub([{ display: 'steel-adjusters' }]);
    const resolve = makeDatabricksRoleResolver({ fetch: fetchMock, host: HOST });
    expect(await resolve(reqWith({ email: 'u@x.com' }))).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('per-user allowlist OVERRIDES the group mapping (and skips SCIM)', async () => {
    // boss@x.com is a group business-user, but the ADJUSTER_USERS override wins.
    const fetchMock = scimStub([{ display: 'steel-business' }]);
    const resolve = makeDatabricksRoleResolver({ fetch: fetchMock, host: HOST });
    expect(await resolve(reqWith({ email: 'boss@x.com', token: 'tok' }))).toBe('adjuster');
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('honours GROUP_SCOPE=workspace (preview SCIM path)', async () => {
    process.env.GROUP_SCOPE = 'workspace';
    const fetchMock = scimStub([{ display: 'steel-adjusters' }]);
    const resolve = makeDatabricksRoleResolver({ fetch: fetchMock, host: HOST });
    await resolve(reqWith({ email: 'u@x.com', token: 'tok' }));
    expect(fetchMock.mock.calls[0][0]).toBe(`${HOST}/api/2.0/preview/scim/v2/Me`);
  });

  it('a SCIM HTTP error is a HARD DENY (throws), and is NOT cached', async () => {
    const fetchMock = scimStub([{ display: 'steel-adjusters' }], { ok: false, status: 500 });
    const resolve = makeDatabricksRoleResolver({ fetch: fetchMock, host: HOST });
    await expect(resolve(reqWith({ email: 'u@x.com', token: 'tok' }))).rejects.toThrow(/HTTP 500/);
    // failure is never cached: a second call hits SCIM again (still denies).
    await expect(resolve(reqWith({ email: 'u@x.com', token: 'tok' }))).rejects.toThrow(/HTTP 500/);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('a SCIM network failure is a HARD DENY (throws), never an open door', async () => {
    const fetchMock = vi.fn(() => Promise.reject(new Error('ECONNRESET')));
    const resolve = makeDatabricksRoleResolver({ fetch: fetchMock, host: HOST });
    await expect(resolve(reqWith({ email: 'u@x.com', token: 'tok' }))).rejects.toThrow(/request failed/);
  });

  it('caches the resolved role: a second call within TTL avoids a second fetch', async () => {
    let clock = 1_000;
    const fetchMock = scimStub([{ display: 'steel-adjusters' }]);
    const resolve = makeDatabricksRoleResolver({
      fetch: fetchMock,
      host: HOST,
      now: () => clock,
      cacheTtlMs: 120_000,
    });
    expect(await resolve(reqWith({ email: 'u@x.com', token: 'tok' }))).toBe('adjuster');
    clock += 60_000; // still within TTL
    expect(await resolve(reqWith({ email: 'u@x.com', token: 'tok2' }))).toBe('adjuster');
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it('cache miss after TTL refetches', async () => {
    let clock = 1_000;
    const fetchMock = scimStub([{ display: 'steel-adjusters' }]);
    const resolve = makeDatabricksRoleResolver({
      fetch: fetchMock,
      host: HOST,
      now: () => clock,
      cacheTtlMs: 1_000,
    });
    expect(await resolve(reqWith({ email: 'u@x.com', token: 'tok' }))).toBe('adjuster');
    clock += 1_001; // past TTL
    expect(await resolve(reqWith({ email: 'u@x.com', token: 'tok' }))).toBe('adjuster');
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
