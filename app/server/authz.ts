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
 * ROLE SOURCE: an explicit per-user allowlist keyed on the authenticated caller's
 * email (env `ADJUSTER_USERS` / `BUSINESS_USERS`, comma-separated). The subject is
 * the OBO user identity (`x-forwarded-email` / `x-forwarded-user`), so resolution is
 * server-side. This is the SOLE role mechanism (see identity.ts). Group-based
 * mapping is intentionally NOT wired: the typed workspace-client `currentUser.me()`
 * in this scaffold does not expose group membership, so a governed group lookup is
 * not available here; the config reflects only what actually enforces. A caller who
 * matches no allowlist is hard-denied by default.
 */

import type { Request, Response, NextFunction } from 'express';

export type Role = 'adjuster' | 'business_user';

export type Action =
  | 'queue'
  | 'cockpit'
  | 'finalize'
  | 'claims_history'
  | 'cockpit_copilot'
  | 'business_dashboard'
  | 'business_chat';

/** The permission matrix — the single source of truth for role → allowed actions. */
export const PERMISSIONS: Record<Role, ReadonlySet<Action>> = {
  adjuster: new Set<Action>(['queue', 'cockpit', 'finalize', 'claims_history', 'cockpit_copilot']),
  business_user: new Set<Action>(['business_dashboard', 'business_chat', 'claims_history']),
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
    next();
  };
}
