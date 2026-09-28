import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import type { Request } from 'express';
import {
  getUserIdentity,
  decidedBy,
  makeDatabricksRoleResolver,
  RoleCache,
  type ScimResponse,
  type ScimGroup,
} from './identity';
import type { Role, RoleSet } from './authz';

/** Deterministically compare a resolved role SET as a sorted array. */
function roles(set: RoleSet): Role[] {
  return [...set].sort();
}

function reqWith(opts: { email?: string; token?: string } = {}): Request {
  const headers: Record<string, string> = {};
  if (opts.email) headers['x-forwarded-email'] = opts.email;
  if (opts.token) headers['x-forwarded-access-token'] = opts.token;
  const req: Pick<Request, 'headers'> = { headers };
  return req as Request;
}

/** Restore a process.env key, DELETING it when the saved value was undefined (assigning
 *  undefined would coerce to the literal string 'undefined'). */
function restoreEnv(key: string, value: string | undefined): void {
  if (value === undefined) delete process.env[key];
  else process.env[key] = value;
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
    // `both@x.com` is in BOTH lists — the multi-role case that used to resolve to
    // adjuster-only (the bug this change fixes).
    process.env.ADJUSTER_USERS = 'adj1@x.com, Adj2@x.com, both@x.com';
    process.env.BUSINESS_USERS = 'biz1@x.com, both@x.com';
    delete process.env.ADJUSTER_GROUPS;
    delete process.env.BUSINESS_GROUPS;
  });
  afterEach(() => {
    restoreEnv('ADJUSTER_USERS', saved.adj);
    restoreEnv('BUSINESS_USERS', saved.biz);
  });

  it('assigns adjuster / business_user from the allowlists (case-insensitive), no token or fetch needed', async () => {
    const fetchMock = scimStub([]);
    const resolve = makeDatabricksRoleResolver({ fetch: fetchMock, host: HOST });
    expect(roles(await resolve(reqWith({ email: 'adj1@x.com' })))).toEqual(['adjuster']);
    expect(roles(await resolve(reqWith({ email: 'ADJ2@X.COM' })))).toEqual(['adjuster']);
    expect(roles(await resolve(reqWith({ email: 'biz1@x.com' })))).toEqual(['business_user']);
    expect(fetchMock).not.toHaveBeenCalled(); // allowlist short-circuits before SCIM
  });

  it('returns the FULL set for a user in BOTH allowlists (the bug fix): {adjuster, business_user}', async () => {
    const fetchMock = scimStub([]);
    const resolve = makeDatabricksRoleResolver({ fetch: fetchMock, host: HOST });
    // Previously this resolved to adjuster only (first match wins) and the user could
    // never reach the Business surface; now both roles are returned.
    expect(roles(await resolve(reqWith({ email: 'both@x.com' })))).toEqual(['adjuster', 'business_user']);
    expect(roles(await resolve(reqWith({ email: 'BOTH@X.COM' })))).toEqual(['adjuster', 'business_user']);
    expect(fetchMock).not.toHaveBeenCalled(); // allowlist is complete => no SCIM
  });

  it('hard-denies (empty set) an unlisted user with no token, and a missing identity', async () => {
    const fetchMock = scimStub([]);
    const resolve = makeDatabricksRoleResolver({ fetch: fetchMock, host: HOST });
    expect(roles(await resolve(reqWith({ email: 'nobody@x.com' })))).toEqual([]);
    expect(roles(await resolve(reqWith()))).toEqual([]);
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
    restoreEnv('ADJUSTER_USERS', saved.adjU);
    restoreEnv('BUSINESS_USERS', saved.bizU);
    restoreEnv('ADJUSTER_GROUPS', saved.adjG);
    restoreEnv('BUSINESS_GROUPS', saved.bizG);
    restoreEnv('GROUP_SCOPE', saved.scope);
  });

  it('maps a matching group -> adjuster (by display name)', async () => {
    const fetchMock = scimStub([{ display: 'Steel-Adjusters', value: 'g-1' }]);
    const resolve = makeDatabricksRoleResolver({ fetch: fetchMock, host: HOST });
    expect(roles(await resolve(reqWith({ email: 'u@x.com', token: 'tok' })))).toEqual(['adjuster']);
    expect(fetchMock).toHaveBeenCalledOnce();
    // default GROUP_SCOPE is `account`
    expect(fetchMock.mock.calls[0][0]).toBe(`${HOST}/api/2.0/account/scim/v2/Me`);
    expect(fetchMock.mock.calls[0][1].headers.Authorization).toBe('Bearer tok');
  });

  it('maps a matching group -> business_user (by display name)', async () => {
    const fetchMock = scimStub([{ display: 'steel-business', value: 'g-2' }]);
    const resolve = makeDatabricksRoleResolver({ fetch: fetchMock, host: HOST });
    expect(roles(await resolve(reqWith({ email: 'u@x.com', token: 'tok' })))).toEqual(['business_user']);
  });

  it('returns BOTH roles for a member of an adjuster AND a business group (union)', async () => {
    const fetchMock = scimStub([
      { display: 'steel-adjusters', value: 'g-1' },
      { display: 'steel-business', value: 'g-2' },
    ]);
    const resolve = makeDatabricksRoleResolver({ fetch: fetchMock, host: HOST });
    expect(roles(await resolve(reqWith({ email: 'u@x.com', token: 'tok' })))).toEqual(['adjuster', 'business_user']);
  });

  it('matches a group by id (value) as well as display', async () => {
    // display does not match any configured group; the group id (value) does.
    const fetchMock = scimStub([{ display: 'Some Unrelated Name', value: '100-adj-id' }]);
    const resolve = makeDatabricksRoleResolver({ fetch: fetchMock, host: HOST });
    expect(roles(await resolve(reqWith({ email: 'u@x.com', token: 'tok' })))).toEqual(['adjuster']);
  });

  it('hard-denies (empty set) when no group matches', async () => {
    const fetchMock = scimStub([{ display: 'random-team', value: 'g-9' }]);
    const resolve = makeDatabricksRoleResolver({ fetch: fetchMock, host: HOST });
    expect(roles(await resolve(reqWith({ email: 'u@x.com', token: 'tok' })))).toEqual([]);
  });

  it('hard-denies (empty set) when the OBO token is missing, without calling SCIM', async () => {
    const fetchMock = scimStub([{ display: 'steel-adjusters' }]);
    const resolve = makeDatabricksRoleResolver({ fetch: fetchMock, host: HOST });
    expect(roles(await resolve(reqWith({ email: 'u@x.com' })))).toEqual([]);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('per-user allowlist OVERRIDES the group mapping (and skips SCIM)', async () => {
    // boss@x.com is a group business-user, but the ADJUSTER_USERS override wins.
    const fetchMock = scimStub([{ display: 'steel-business' }]);
    const resolve = makeDatabricksRoleResolver({ fetch: fetchMock, host: HOST });
    expect(roles(await resolve(reqWith({ email: 'boss@x.com', token: 'tok' })))).toEqual(['adjuster']);
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

  it('a slow SCIM /Me that trips the AbortSignal.timeout is a HARD DENY (throws)', async () => {
    // The stub never resolves on its own; it rejects only when the injected abort
    // signal fires — exercising the real AbortSignal.timeout(scimTimeoutMs) path.
    const fetchMock = vi.fn(
      (_url: string, init: { headers: Record<string, string>; signal?: AbortSignal }) =>
        new Promise<ScimResponse>((_resolve, reject) => {
          init.signal?.addEventListener('abort', () => reject(new Error('The operation was aborted')));
        })
    );
    const resolve = makeDatabricksRoleResolver({ fetch: fetchMock, host: HOST, scimTimeoutMs: 5 });
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
    expect(roles(await resolve(reqWith({ email: 'u@x.com', token: 'tok' })))).toEqual(['adjuster']);
    clock += 60_000; // still within TTL
    expect(roles(await resolve(reqWith({ email: 'u@x.com', token: 'tok2' })))).toEqual(['adjuster']);
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
    expect(roles(await resolve(reqWith({ email: 'u@x.com', token: 'tok' })))).toEqual(['adjuster']);
    clock += 1_001; // past TTL
    expect(roles(await resolve(reqWith({ email: 'u@x.com', token: 'tok' })))).toEqual(['adjuster']);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});

describe('RoleCache (bounded + LRU + TTL)', () => {
  it('never exceeds the cap and evicts the oldest (LRU) entries', () => {
    const cache = new RoleCache<Role>(3, 60_000, () => 1_000);
    for (let i = 0; i < 6; i++) cache.set(`u${i}`, 'adjuster'); // insert > cap distinct users
    expect(cache.size).toBe(3); // size stays <= cap
    // the three oldest were evicted; only the newest three survive.
    expect(cache.get('u0')).toBeNull();
    expect(cache.get('u1')).toBeNull();
    expect(cache.get('u2')).toBeNull();
    expect(cache.get('u3')).toBe('adjuster');
    expect(cache.get('u4')).toBe('adjuster');
    expect(cache.get('u5')).toBe('adjuster');
  });

  it('a cache hit refreshes LRU order, sparing a recently-used entry from eviction', () => {
    const cache = new RoleCache<Role>(3, 60_000, () => 1_000);
    cache.set('a', 'adjuster');
    cache.set('b', 'business_user');
    cache.set('c', 'adjuster'); // order oldest->newest: a, b, c
    expect(cache.get('a')).toBe('adjuster'); // touch a -> order: b, c, a
    cache.set('d', 'adjuster'); // at capacity -> evict oldest (b)
    expect(cache.size).toBe(3);
    expect(cache.get('b')).toBeNull(); // b was the LRU, evicted
    expect(cache.get('a')).toBe('adjuster'); // a survived because it was touched
    expect(cache.get('d')).toBe('adjuster');
  });

  it('purges an expired entry on access (and shrinks the cache)', () => {
    let clock = 0;
    const cache = new RoleCache<Role>(10, 1_000, () => clock);
    cache.set('a', 'adjuster');
    expect(cache.size).toBe(1);
    clock = 1_000; // at expiry boundary (expiresAt <= now)
    expect(cache.get('a')).toBeNull(); // expired -> hard miss
    expect(cache.size).toBe(0); // and purged from the map
  });
});
