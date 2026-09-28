/**
 * OBO user identity + role resolution wiring.
 *
 * Databricks Apps inject the calling user's identity on every request via
 * `x-forwarded-email` / `x-forwarded-user`, plus the user's OBO access token via
 * `x-forwarded-access-token`. The email is the audit subject recorded as `decided_by`
 * on a finalization; the OBO token is what we present to Databricks on the user's
 * behalf to resolve their governed group membership.
 *
 * MULTI-ROLE: a caller may hold BOTH roles. The resolver returns the FULL role SET,
 * not the first match — so a login in both allowlists (or a member of both an adjuster
 * and a business group) resolves to `{adjuster, business_user}` and can reach BOTH
 * surfaces. An empty set is a hard deny.
 *
 * ROLE RESOLUTION (see `makeDatabricksRoleResolver`), precedence exactly:
 *   1. Per-user allowlist OVERRIDE — `ADJUSTER_USERS` / `BUSINESS_USERS` (CSV email).
 *      An explicit escape hatch, checked first, no token or network required. The
 *      caller's allowlist roles are UNIONED (both lists => both roles); if this yields
 *      a non-empty set it is returned as-is and SCIM is skipped.
 *   2. OBO user token — `x-forwarded-access-token`. Absent => hard deny (empty set).
 *   3. Group membership (PRIMARY) — a raw REST GET to the SCIM `/Me` endpoint with the
 *      END USER's OBO token; EVERY matching group contributes, so a member of both an
 *      adjuster and a business group resolves to both roles (`ADJUSTER_GROUPS` /
 *      `BUSINESS_GROUPS`).
 *   4. No group match => hard deny (empty set).
 *
 * The group lookup needs no extra `user_api_scope` and no admin grant: the default
 * `iam.current-user:read` capability of any OBO token covers `/Me`. Namespace is
 * `GROUP_SCOPE` (`account` | `workspace`), DEFAULT `account`.
 *
 * Resilience: `fetch`, the clock, and the host are injectable so the resolver is unit
 * testable. A per-user short-TTL cache stores the RESOLVED ROLE SET only — never the
 * token, never a failure. Any SCIM error/timeout throws, which the authz middleware
 * turns into a 403: a network/SCIM failure must never open the door.
 */

import type { Request } from 'express';
import { z } from 'zod';
import { type Role, type RoleSet, type RoleResolver } from './authz';

export interface UserIdentity {
  email: string | null;
  user: string | null;
}

function header(req: Request, name: string): string | null {
  const v = req.headers[name];
  if (Array.isArray(v)) return v[0] ?? null;
  return typeof v === 'string' && v.length > 0 ? v : null;
}

/** Extract the OBO user identity from the Databricks-Apps forwarded headers. */
export function getUserIdentity(req: Request): UserIdentity {
  return {
    email: header(req, 'x-forwarded-email'),
    user: header(req, 'x-forwarded-user') ?? header(req, 'x-forwarded-email'),
  };
}

/** The `decided_by` value for a finalization: the OBO user email, or null if absent. */
export function decidedBy(req: Request): string | null {
  return getUserIdentity(req).email;
}

/** The OBO user access token forwarded by the Apps proxy, or null if absent. */
function oboToken(req: Request): string | null {
  return header(req, 'x-forwarded-access-token');
}

function csv(name: string): string[] {
  return (process.env[name] ?? '')
    .split(',')
    .map((s) => s.trim().toLowerCase())
    .filter(Boolean);
}

// --- Group-based resolution (SCIM /Me) --------------------------------------

export type GroupScope = 'account' | 'workspace';

/** SCIM `/Me` path per namespace. Both hang off the same workspace/account host. */
const SCIM_ME_PATH: Record<GroupScope, string> = {
  account: '/api/2.0/account/scim/v2/Me',
  workspace: '/api/2.0/preview/scim/v2/Me',
};

const DEFAULT_CACHE_TTL_MS = 120_000;
const DEFAULT_SCIM_TIMEOUT_MS = 5_000;
/** Hard cap on cached role entries, so the cache is bounded in memory (LRU eviction). */
const DEFAULT_CACHE_MAX_ENTRIES = 5_000;

/** Minimal structural view of a fetch Response — all the resolver consumes. */
export interface ScimResponse {
  ok: boolean;
  status: number;
  json(): Promise<unknown>;
}

/** Injectable fetch. The global `fetch` is structurally assignable to this. */
export type FetchLike = (
  url: string,
  init: { headers: Record<string, string>; signal?: AbortSignal }
) => Promise<ScimResponse>;

export interface RoleResolverOptions {
  /** Override the HTTP client (tests inject a stub). Defaults to the global fetch. */
  fetch?: FetchLike;
  /** Override the clock for the TTL cache (tests inject). Defaults to Date.now. */
  now?: () => number;
  /** Override the SCIM host. Defaults to the SDK-config host (DATABRICKS_HOST). */
  host?: string;
  /** Resolved-role cache TTL in ms. Defaults to 120s. */
  cacheTtlMs?: number;
  /** Max cached role entries before LRU eviction. Defaults to 5000. */
  cacheMaxEntries?: number;
  /** SCIM request timeout in ms. Defaults to 5s. */
  scimTimeoutMs?: number;
}

/**
 * SCIM `/Me` body — we read only the direct `groups` memberships. Unknown fields are
 * ignored (z.object is non-strict), so the shape survives SCIM additions.
 */
const scimMeSchema = z.object({
  groups: z
    .array(
      z.object({
        display: z.string().optional(),
        value: z.string().optional(),
        type: z.string().optional(),
      })
    )
    .optional(),
});

export type ScimGroup = { display?: string; value?: string; type?: string };

/** Read GROUP_SCOPE (default `account`); anything but `workspace` is `account`. */
function readGroupScope(): GroupScope {
  return (process.env.GROUP_SCOPE ?? 'account').trim().toLowerCase() === 'workspace' ? 'workspace' : 'account';
}

/**
 * Resolve the SCIM host: an explicit override, else DATABRICKS_HOST (which is exactly
 * what the Databricks SDK config derives its host from, and which the Apps runtime
 * always injects). Normalized to `https://<host>` with no trailing slash.
 */
function resolveHost(override?: string): string | null {
  const raw = (override ?? process.env.DATABRICKS_HOST ?? '').trim();
  if (!raw) return null;
  const withScheme = /^https?:\/\//i.test(raw) ? raw : `https://${raw}`;
  return withScheme.replace(/\/+$/, '');
}

const defaultFetch: FetchLike = (url, init) => globalThis.fetch(url, init);

/**
 * A bounded, TTL'd, LRU cache keyed by user. Never holds tokens or failures. `Map`
 * insertion order == LRU order: `get` re-inserts a live hit (newest), purges an expired
 * entry on access; `set` evicts the oldest key(s) when at capacity before inserting. So
 * the cache can never exceed `maxEntries` and cannot grow without bound even under a
 * churn of distinct users. Generic over the cached value `V` — the resolver caches the
 * resolved role SET (`readonly Role[]`); the LRU/TTL mechanics are value-agnostic.
 */
export class RoleCache<V> {
  private readonly map = new Map<string, { value: V; expiresAt: number }>();

  constructor(
    private readonly maxEntries: number,
    private readonly ttlMs: number,
    private readonly now: () => number
  ) {}

  /** Current live entry count (for tests / introspection). */
  get size(): number {
    return this.map.size;
  }

  /** Return the cached value, purging it if expired and refreshing LRU order on a hit. */
  get(key: string): V | null {
    const hit = this.map.get(key);
    if (!hit) return null;
    if (hit.expiresAt <= this.now()) {
      this.map.delete(key); // purge expired on access
      return null;
    }
    this.map.delete(key); // re-insert to move to the newest LRU position
    this.map.set(key, hit);
    return hit.value;
  }

  /** Insert/refresh a value, evicting the oldest (LRU) entries while at capacity. */
  set(key: string, value: V): void {
    this.map.delete(key); // ensure a refresh lands at the newest position
    while (this.map.size >= this.maxEntries) {
      const oldest = this.map.keys().next().value;
      if (oldest === undefined) break;
      this.map.delete(oldest);
    }
    this.map.set(key, { value, expiresAt: this.now() + this.ttlMs });
  }
}

/**
 * Build the role resolver from app configuration. Returns the caller's FULL role SET.
 *
 * PRIMARY mechanism: governed group membership resolved server-side from the OBO
 * user's token via SCIM `/Me` (`ADJUSTER_GROUPS` / `BUSINESS_GROUPS`, matched against
 * each direct group's `display` or `value`); EVERY matching group contributes, so a
 * member of both an adjuster and a business group resolves to both roles. Per-user
 * allowlists (`ADJUSTER_USERS` / `BUSINESS_USERS`) are an OVERRIDE checked first — the
 * escape hatch for individuals the group config can't (yet) cover; a caller in both
 * lists gets both roles. When the allowlist yields any role it is returned as-is
 * (SCIM is skipped), preserving the escape hatch's independence from the network so an
 * explicitly-listed user is never denied by a SCIM outage. A caller matched by neither
 * resolves to the empty set and is hard-denied by the authz middleware; any SCIM error
 * throws (never opens the door).
 */
export function makeDatabricksRoleResolver(opts: RoleResolverOptions = {}): RoleResolver {
  const adjusterUsers = new Set(csv('ADJUSTER_USERS'));
  const businessUsers = new Set(csv('BUSINESS_USERS'));
  const adjusterGroups = new Set(csv('ADJUSTER_GROUPS'));
  const businessGroups = new Set(csv('BUSINESS_GROUPS'));
  const scope = readGroupScope();
  const doFetch = opts.fetch ?? defaultFetch;
  const now = opts.now ?? Date.now;
  const cacheTtlMs = opts.cacheTtlMs ?? DEFAULT_CACHE_TTL_MS;
  const cacheMaxEntries = opts.cacheMaxEntries ?? DEFAULT_CACHE_MAX_ENTRIES;
  const scimTimeoutMs = opts.scimTimeoutMs ?? DEFAULT_SCIM_TIMEOUT_MS;

  // Bounded per-user cache of the RESOLVED (non-empty) role set — capped + LRU + TTL,
  // so it never grows without bound. Never stores the token, never a failure.
  const cache = new RoleCache<readonly Role[]>(cacheMaxEntries, cacheTtlMs, now);

  // Deploy-time verification aid: log the direct-group count ONCE on the first real
  // SCIM /Me success, so we can confirm the narrowly-scoped OBO token actually returns
  // a populated groups[] under iam.current-user:read. Never logs group names/ids.
  let loggedGroupCount = false;

  /** Map SCIM direct-group memberships to the FULL set of matching roles. */
  function groupRoles(groups: ScimGroup[]): Set<Role> {
    // Direct memberships only — SCIM /Me returns direct memberships; we never expand
    // nested groups. Defensively drop any explicitly-indirect entry.
    const direct = groups.filter((g) => (g.type ?? 'direct').toLowerCase() !== 'indirect');
    const tokensOf = (g: ScimGroup): string[] =>
      [g.display?.toLowerCase(), g.value?.toLowerCase()].filter((t): t is string => Boolean(t));
    const roles = new Set<Role>();
    if (direct.some((g) => tokensOf(g).some((t) => adjusterGroups.has(t)))) roles.add('adjuster');
    if (direct.some((g) => tokensOf(g).some((t) => businessGroups.has(t)))) roles.add('business_user');
    return roles;
  }

  /** GET SCIM /Me with the user's OBO token and map their groups to the role set. */
  async function resolveGroupRoles(token: string): Promise<Set<Role>> {
    const host = resolveHost(opts.host);
    if (!host) throw new Error('SCIM host unresolved (set DATABRICKS_HOST)');
    const url = `${host}${SCIM_ME_PATH[scope]}`;
    let res: ScimResponse;
    try {
      res = await doFetch(url, {
        headers: { Authorization: `Bearer ${token}`, Accept: 'application/scim+json' },
        signal: AbortSignal.timeout(scimTimeoutMs),
      });
    } catch (err) {
      throw new Error(`SCIM /Me request failed: ${(err as Error).message}`);
    }
    if (!res.ok) throw new Error(`SCIM /Me returned HTTP ${res.status}`);
    const body = scimMeSchema.parse(await res.json());
    const groups = body.groups ?? [];
    if (!loggedGroupCount) {
      loggedGroupCount = true;
      console.info(`[identity] SCIM /Me (${scope}) returned ${groups.length} direct group(s) for the OBO user`);
    }
    return groupRoles(groups);
  }

  return async function resolveRoles(req: Request): Promise<RoleSet> {
    const email = getUserIdentity(req).email?.toLowerCase() ?? null;

    // (1) Per-user allowlist OVERRIDE — the escape hatch, before groups/network. Union
    // both lists so a login in both resolves to BOTH roles; non-empty => skip SCIM.
    if (email) {
      const allow = new Set<Role>();
      if (adjusterUsers.has(email)) allow.add('adjuster');
      if (businessUsers.has(email)) allow.add('business_user');
      if (allow.size > 0) return allow;
    }

    // (2) OBO user token is required for the group lookup; absent => hard deny.
    const token = oboToken(req);
    if (!token) return new Set<Role>();

    // (3) Bounded short-TTL cache of the resolved (non-empty) group role set, per user.
    if (email) {
      const cached = cache.get(email);
      if (cached) return new Set(cached);
    }

    // (4) Governed group membership via SCIM /Me. Throws on any error => 403.
    const roles = await resolveGroupRoles(token);

    // Cache only a successful (non-empty) resolution — never a deny, never a failure.
    if (roles.size > 0 && email) cache.set(email, [...roles]);
    return roles; // empty => no matching group => hard-deny by default.
  };
}
