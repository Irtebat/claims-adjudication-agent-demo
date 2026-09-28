/**
 * OBO user identity + role resolution wiring.
 *
 * Databricks Apps inject the calling user's identity on every request via
 * `x-forwarded-email` / `x-forwarded-user` (and `x-forwarded-access-token` for OBO
 * token passthrough to governed surfaces). The email is the audit subject recorded
 * as `decided_by` on a finalization, and the subject for role resolution.
 */

import type { Request } from 'express';
import { type Role, resolveRoleFromGroups, type RoleResolver } from './authz';

export interface UserIdentity {
  email: string | null;
  user: string | null;
}

/** Extract the OBO user identity from the Databricks-Apps forwarded headers. */
export function getUserIdentity(req: Request): UserIdentity {
  const header = (name: string): string | null => {
    const v = req.headers[name];
    if (Array.isArray(v)) return v[0] ?? null;
    return typeof v === 'string' && v.length > 0 ? v : null;
  };
  return {
    email: header('x-forwarded-email'),
    user: header('x-forwarded-user') ?? header('x-forwarded-email'),
  };
}

/** The `decided_by` value for a finalization: the OBO user email, or null if absent. */
export function decidedBy(req: Request): string | null {
  return getUserIdentity(req).email;
}

function csv(name: string): string[] {
  return (process.env[name] ?? '')
    .split(',')
    .map((s) => s.trim().toLowerCase())
    .filter(Boolean);
}

/**
 * Build the production role resolver from app configuration.
 *
 * Resolution order for a request's OBO user (by email):
 *   1. Explicit per-user allowlists — env `ADJUSTER_USERS` / `BUSINESS_USERS`
 *      (comma-separated emails). Convenient for demos and pinning specific users.
 *   2. Databricks group membership — env `ADJUSTER_GROUPS` / `BUSINESS_GROUPS`
 *      (comma-separated group display names) resolved against the caller's groups
 *      via the injected `groupLookup`. In production wire `groupLookup` to a SCIM /
 *      workspace-client group query for the user; it is injected (not hard-coded) so
 *      this module stays unit-testable and does not fabricate an SDK signature.
 *
 * Returns null (=> 403) when the caller matches neither role.
 */
export function makeDatabricksRoleResolver(opts?: {
  groupLookup?: (email: string) => Promise<readonly string[]>;
}): RoleResolver {
  const adjusterUsers = new Set(csv('ADJUSTER_USERS'));
  const businessUsers = new Set(csv('BUSINESS_USERS'));
  const adjusterGroups = csv('ADJUSTER_GROUPS');
  const businessGroups = csv('BUSINESS_GROUPS');
  const groupLookup = opts?.groupLookup;

  return async function resolveRole(req: Request): Promise<Role | null> {
    const email = getUserIdentity(req).email?.toLowerCase();
    if (!email) return null;
    if (adjusterUsers.has(email)) return 'adjuster';
    if (businessUsers.has(email)) return 'business_user';
    if (groupLookup && (adjusterGroups.length || businessGroups.length)) {
      const groups = await groupLookup(email);
      return resolveRoleFromGroups(groups, { adjusterGroups, businessGroups });
    }
    return null;
  };
}
