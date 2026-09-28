/**
 * Server-side authorization for the claims cockpit (Wave 7, Stage A).
 *
 * Two roles, enforced on EVERY backend route (never in the client):
 *   - `adjuster`      — queue, cockpit detail, decision/finalize, claims history,
 *                       cockpit copilot (operational Genie).
 *   - `business_user` — business dashboard data, business chat (gold-analytics
 *                       Genie), claims history.
 *
 * Explicit denials from the contract: Business Users are DENIED the
 * decision/finalize/queue endpoints; Adjusters are DENIED the business-dashboard
 * data endpoint. Both roles may read claims history.
 *
 * ROLE SOURCE (server-side; see identity.ts): governed GROUP membership is the
 * PRIMARY mechanism — the OBO user's token (`x-forwarded-access-token`) is presented
 * to SCIM `/Me` and their direct groups are mapped to a role via env
 * `ADJUSTER_GROUPS` / `BUSINESS_GROUPS`. A per-user allowlist (`ADJUSTER_USERS` /
 * `BUSINESS_USERS`, keyed on `x-forwarded-email`) is an OVERRIDE checked first — the
 * escape hatch for callers the group config can't cover. A caller matched by neither
 * is hard-denied by default, and any SCIM lookup failure hard-denies (never opens).
 */

import type { Request, Response, NextFunction } from 'express';

export type Role = 'adjuster' | 'business_user';

export type Action =
  | 'identity'
  | 'queue'
  | 'cockpit'
  | 'finalize'
  | 'claims_history'
  | 'cockpit_copilot'
  | 'business_dashboard'
  | 'business_chat';

/**
 * The permission matrix — the single source of truth for role → allowed actions.
 * `identity` (the /api/whoami echo of the caller's own resolved role) is allowed to
 * BOTH roles; it exposes nothing beyond who the caller already is.
 */
export const PERMISSIONS: Record<Role, ReadonlySet<Action>> = {
  adjuster: new Set<Action>(['identity', 'queue', 'cockpit', 'finalize', 'claims_history', 'cockpit_copilot']),
  business_user: new Set<Action>(['identity', 'business_dashboard', 'business_chat', 'claims_history']),
};

/** True iff `role` may perform `action`. Pure. */
export function authorize(role: Role | null, action: Action): boolean {
  if (!role) return false;
  return PERMISSIONS[role].has(action);
}

/**
 * Map an HTTP method + path to the backend action it exercises, or null when the
 * path is not a guarded API surface (static assets, /health, /api/genie replay of
 * an already-authorized conversation still maps to its space's action below).
 */
export function actionForPath(method: string, path: string): Action | null {
  const p = path.replace(/\/+$/, '');
  // The signed-in caller reading back their own identity + resolved role (both roles).
  if (p === '/api/whoami') return 'identity';
  if (p === '/api/queue') return 'queue';
  if (/^\/api\/claims\/[^/]+\/finalize$/.test(p) && method === 'POST') return 'finalize';
  if (/^\/api\/claims\/[^/]+$/.test(p) && method === 'GET') return 'cockpit';
  if (p === '/api/history') return 'claims_history';
  // Genie surfaces are auto-mounted by the plugin under /api/genie/:alias/... —
  // guard them by alias so a Business User cannot reach the cockpit copilot and an
  // Adjuster cannot reach the business chat.
  if (p.startsWith('/api/genie/cockpit')) return 'cockpit_copilot';
  if (p.startsWith('/api/genie/business')) return 'business_chat';
  if (p.startsWith('/api/business/dashboard')) return 'business_dashboard';
  // The analytics plugin (warehouse) backs ONLY the business dashboard here.
  if (p.startsWith('/api/analytics')) return 'business_dashboard';
  return null;
}

/** A request-scoped role resolver. Injected so the middleware is unit-testable. */
export type RoleResolver = (req: Request) => Promise<Role | null> | Role | null;

/**
 * Express middleware enforcing the permission matrix. Registered GLOBALLY (no mount
 * prefix) in onPluginsReady so `req.path` is the full path and the guard runs before
 * the deferred plugin-route mount — covering the Genie/analytics plugin routes too.
 *
 * Default-DENY: a request under `/api/` that maps to no known action is rejected
 * (403), so a future endpoint added without a matrix entry cannot silently bypass
 * authz. Non-API paths (static assets, `/health`) pass through untouched.
 */
export function makeAuthz(resolveRole: RoleResolver) {
  return async function authz(req: Request, res: Response, next: NextFunction): Promise<void> {
    const action = actionForPath(req.method, req.path);
    if (action === null) {
      if (req.path.startsWith('/api/')) {
        // Unmapped API route — fail closed rather than leak an unguarded surface.
        res.status(403).json({ error: 'forbidden', reason: 'unmapped_api_route' });
        return;
      }
      next();
      return;
    }
    let role: Role | null;
    try {
      role = await resolveRole(req);
    } catch (err) {
      // A failed governed role lookup is a hard deny, never an open door.
      console.error('[authz] role resolution failed:', (err as Error).message);
      res.status(403).json({ error: 'forbidden', reason: 'role_resolution_failed' });
      return;
    }
    if (!authorize(role, action)) {
      res.status(403).json({
        error: 'forbidden',
        reason: role ? 'role_not_permitted' : 'no_role',
        action,
      });
      return;
    }
    // Expose the resolved role to downstream handlers (e.g. /api/whoami) so they need
    // not re-run the SCIM lookup. `res.locals` is always present under Express; the
    // guard keeps the middleware usable with the lightweight test mocks.
    if (res.locals) (res.locals as Record<string, unknown>).role = role;
    next();
  };
}
