import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import type { Request } from 'express';
import { getUserIdentity, decidedBy, makeDatabricksRoleResolver } from './identity';

function reqWith(email?: string): Request {
  const headers: Record<string, string> = {};
  if (email) headers['x-forwarded-email'] = email;
  const req: Pick<Request, 'headers'> = { headers };
  return req as Request;
}

describe('getUserIdentity / decidedBy', () => {
  it('extracts the OBO email from x-forwarded-email', () => {
    expect(getUserIdentity(reqWith('a@x.com')).email).toBe('a@x.com');
    expect(decidedBy(reqWith('a@x.com'))).toBe('a@x.com');
  });
  it('is null when the header is absent', () => {
    expect(getUserIdentity(reqWith()).email).toBeNull();
    expect(decidedBy(reqWith())).toBeNull();
  });
});

describe('makeDatabricksRoleResolver (per-user allowlist — the sole role mechanism)', () => {
  const saved = { adj: process.env.ADJUSTER_USERS, biz: process.env.BUSINESS_USERS };
  beforeEach(() => {
    process.env.ADJUSTER_USERS = 'adj1@x.com, Adj2@x.com';
    process.env.BUSINESS_USERS = 'biz1@x.com';
  });
  afterEach(() => {
    process.env.ADJUSTER_USERS = saved.adj;
    process.env.BUSINESS_USERS = saved.biz;
  });

  it('assigns adjuster / business_user from the allowlists (case-insensitive)', async () => {
    const resolve = makeDatabricksRoleResolver();
    expect(await resolve(reqWith('adj1@x.com'))).toBe('adjuster');
    expect(await resolve(reqWith('ADJ2@X.COM'))).toBe('adjuster');
    expect(await resolve(reqWith('biz1@x.com'))).toBe('business_user');
  });

  it('hard-denies (null) an unlisted user and a missing identity', async () => {
    const resolve = makeDatabricksRoleResolver();
    expect(await resolve(reqWith('nobody@x.com'))).toBeNull();
    expect(await resolve(reqWith())).toBeNull();
  });
});
