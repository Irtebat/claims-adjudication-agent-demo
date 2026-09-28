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
 * MULTI-ROLE: a single caller may hold BOTH roles (e.g. a login in both allowlists,
 * or a member of both an adjuster and a business group). The resolver returns the
 * caller's FULL role SET (see identity.ts); authorization is the UNION of the
 * per-role permissions — an action is allowed iff SOME role the caller ACTUALLY holds
 * permits it. The set is the server's own resolution of who the caller is; it is
 * NEVER derived from a client-supplied "active role". The in-app persona switch is a
 * VIEW preference only, so a single-role caller can never escalate to the other role
 * by asserting it from the client — the middleware ignores any such header entirely.
 *
 * ROLE SOURCE (server-side; see identity.ts): governed GROUP membership is the
 * PRIMARY mechanism — the OBO user's token (`x-forwarded-access-token`) is presented
 * to SCIM `/Me` and their direct groups are mapped to roles via env
 * `ADJUSTER_GROUPS` / `BUSINESS_GROUPS`. A per-user allowlist (`ADJUSTER_USERS` /
 * `BUSINESS_USERS`, keyed on `x-forwarded-email`) is an OVERRIDE checked first — the
 * escape hatch for callers the group config can't cover. A caller matched by neither
 * is hard-denied by default, and any SCIM lookup failure hard-denies (never opens).
 */

import type { Request, Response, NextFunction } from 'express';

export type Role = 'adjuster' | 'business_user';

/** The full set of roles a caller actually holds. Empty => hard deny. */
export type RoleSet = ReadonlySet<Role>;

export type Action =
  | 'identity'
  | 'queue'
  | 'cockpit'
  | 'finalize'
  | 'claims_history'
  | 'cockpit_copilot'
  | 'history_chat'
  | 'business_dashboard'
  | 'business_chat';

/**
 * The permission matrix — the single source of truth for role → allowed actions.
 * `identity` (the /api/whoami echo of the caller's own resolved role) is allowed to
 * BOTH roles; it exposes nothing beyond who the caller already is.
 *
 * `history_chat` is the OPERATIONAL Genie assistant scoped to the Claims-History
 * surface. It is allowed to BOTH roles (everyone who can see Claims History), and is
 * DISTINCT from `cockpit_copilot` so exposing it to Business Users does not widen the
 * adjuster-only cockpit copilot. Both run OBO, so answers still respect each caller's
 * own Unity Catalog grants.
 */
export const PERMISSIONS: Record<Role, ReadonlySet<Action>> = {
  adjuster: new Set<Action>([
    'identity',
    'queue',
    'cockpit',
    'finalize',
    'claims_history',
    'cockpit_copilot',
    'history_chat',
  ]),
  business_user: new Set<Action>(['identity', 'business_dashboard', 'business_chat', 'claims_history', 'history_chat']),
};

/** True iff the single `role` may perform `action`. Pure. */
export function authorize(role: Role | null, action: Action): boolean {
  if (!role) return false;
  return PERMISSIONS[role].has(action);
}

/**
 * True iff the caller may perform `action` given the FULL set of roles they hold —
 * i.e. SOME actual role permits it (the union of per-role permissions). This is the
 * only authorization primitive the request path uses. It takes the server-resolved
 * set; it must never be fed a client-asserted "active role". An empty/absent set is a
 * hard deny, so a caller who resolved to no role cannot do anything.
 */
export function authorizeSet(roles: RoleSet | null | undefined, action: Action): boolean {
  if (!roles) return false;
  for (const role of roles) {
    if (PERMISSIONS[role].has(action)) return true;
  }
  return false;
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
  // Adjuster cannot reach the business chat. The `history` alias (operational space,
  // both roles) is separate from `cockpit` (operational space, adjuster only).
  if (p.startsWith('/api/genie/history')) return 'history_chat';
  if (p.startsWith('/api/genie/cockpit')) return 'cockpit_copilot';
  if (p.startsWith('/api/genie/business')) return 'business_chat';
  if (p.startsWith('/api/business/dashboard')) return 'business_dashboard';
  // The analytics plugin (warehouse) backs ONLY the business dashboard here.
  if (p.startsWith('/api/analytics')) return 'business_dashboard';
  return null;
}

/**
 * A request-scoped role resolver. Injected so the middleware is unit-testable. Returns
 * the FULL set of roles the caller holds (empty set => hard deny).
 */
export type RoleResolver = (req: Request) => Promise<RoleSet> | RoleSet;

/**
 * Express middleware enforcing the permission matrix. Registered GLOBALLY (no mount
 * prefix) in onPluginsReady so `req.path` is the full path and the guard runs before
 * the deferred plugin-route mount — covering the Genie/analytics plugin routes too.
 *
 * The guard authorizes against the caller's SERVER-RESOLVED role set only. It reads no
 * client-supplied "active role" — the in-app persona switch is a view preference, so a
 * caller cannot widen their permissions by asserting a role they do not hold. A
 * business-only caller is still 403'd on an adjuster-only action regardless of any
 * header the client sends.
 *
 * Default-DENY: a request under `/api/` that maps to no known action is rejected
 * (403), so a future endpoint added without a matrix entry cannot silently bypass
 * authz. Non-API paths (static assets, `/health`) pass through untouched.
 */
export function makeAuthz(resolveRoles: RoleResolver) {
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
    let roles: RoleSet;
    try {
      roles = await resolveRoles(req);
    } catch (err) {
      // A failed governed role lookup is a hard deny, never an open door.
      console.error('[authz] role resolution failed:', (err as Error).message);
      res.status(403).json({ error: 'forbidden', reason: 'role_resolution_failed' });
      return;
    }
    if (!authorizeSet(roles, action)) {
      res.status(403).json({
        error: 'forbidden',
        reason: roles.size > 0 ? 'role_not_permitted' : 'no_role',
        action,
      });
      return;
    }
    // Expose the resolved role set to downstream handlers (e.g. /api/whoami) so they
    // need not re-run the SCIM lookup. `res.locals` is always present under Express;
    // the guard keeps the middleware usable with the lightweight test mocks.
    if (res.locals) (res.locals as Record<string, unknown>).roles = [...roles];
    next();
  };
}
