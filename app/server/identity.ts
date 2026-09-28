/**
 * OBO user identity + role resolution wiring.
 *
 * Databricks Apps inject the calling user's identity on every request via
 * `x-forwarded-email` / `x-forwarded-user` (and `x-forwarded-access-token` for OBO
 * token passthrough to governed surfaces). The email is the audit subject recorded
 * as `decided_by` on a finalization, and the subject for role resolution.
 */

import type { Request } from 'express';
import { type Role, type RoleResolver } from './authz';

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
 * Build the role resolver from app configuration.
 *
 * SOLE mechanism: an explicit per-user allowlist keyed on the OBO user's email —
 * env `ADJUSTER_USERS` / `BUSINESS_USERS` (comma-separated). This is what actually
 * enforces role-based access server-side, and the config advertises nothing more.
 *
 * Group-based mapping is deliberately NOT wired: the typed experimental
 * workspace-client `currentUser.me()` does not expose group membership, and a SCIM
 * group-enumeration lookup cannot be typed/verified in this scaffold — so wiring it
 * would risk a deploy whose config does not match enforcement. A caller who matches
 * no allowlist resolves to null and is hard-denied by the authz middleware.
 */
export function makeDatabricksRoleResolver(): RoleResolver {
  const adjusterUsers = new Set(csv('ADJUSTER_USERS'));
  const businessUsers = new Set(csv('BUSINESS_USERS'));

  return function resolveRole(req: Request): Role | null {
    const email = getUserIdentity(req).email?.toLowerCase();
    if (!email) return null;
    if (adjusterUsers.has(email)) return 'adjuster';
    if (businessUsers.has(email)) return 'business_user';
    return null;
  };
}
