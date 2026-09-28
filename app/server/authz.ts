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
 * ROLE SOURCE: Databricks group membership. The app is configured (env, injected
 * as an App resource / app.yaml value) with the group name that maps to each role;
 * `resolveRoleFromGroups` maps a caller's Databricks groups to a role. An explicit
 * per-user allowlist (env) is supported as an override for demos. The OBO user
 * identity (x-forwarded-email / x-forwarded-user) is the subject; role resolution
 * itself is a governed lookup, so it must run server-side.
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

/**
 * Resolve a role from a caller's Databricks group memberships, given the
 * app-configured group names. Adjuster takes precedence if a caller is somehow in
 * both groups (least-surprising: the write-capable role is explicit and audited).
 * Returns null when the caller is in neither group.
 */
export function resolveRoleFromGroups(
  groups: readonly string[],
  cfg: { adjusterGroups: readonly string[]; businessGroups: readonly string[] }
): Role | null {
  const set = new Set(groups.map((g) => g.toLowerCase()));
  const inAdjuster = cfg.adjusterGroups.some((g) => set.has(g.toLowerCase()));
  if (inAdjuster) return 'adjuster';
  const inBusiness = cfg.businessGroups.some((g) => set.has(g.toLowerCase()));
  if (inBusiness) return 'business_user';
  return null;
}

/** A request-scoped role resolver. Injected so the middleware is unit-testable. */
export type RoleResolver = (req: Request) => Promise<Role | null> | Role | null;

/**
 * Express middleware enforcing the permission matrix on every guarded /api route.
 * Registered as a global `/api` guard so it runs before plugin-mounted routes.
 * Ungarded paths (null action) pass through. 403 on role/permission failure.
 */
export function makeAuthz(resolveRole: RoleResolver) {
  return async function authz(req: Request, res: Response, next: NextFunction): Promise<void> {
    const action = actionForPath(req.method, req.path);
    if (action === null) {
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
