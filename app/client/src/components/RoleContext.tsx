/**
 * WhoamiProvider — fetches the signed-in user + resolved role SET once from
 * `/api/whoami` and exposes it via WhoamiContext, along with the active persona.
 *
 * Roles are resolved SERVER-SIDE (allowlist / SCIM group membership) and only mirrored
 * here to shape navigation; every backend route independently re-enforces
 * authorization, so the client role is a convenience, never the control. An empty role
 * set means the caller matched no role and is treated as unauthorized.
 *
 * A caller holding BOTH roles gets an in-app persona switch. The active role is a VIEW
 * preference: it seeds from the server's `defaultRole`, persists for the browser session
 * (sessionStorage, keyed per user), and can only be set to a role the caller actually
 * holds — a stored value the user no longer holds is discarded on load. Switching never
 * changes the caller's permissions; it only changes which surface the UI presents.
 *
 * The hooks (useWhoami / useRole / useActiveRole / ...) live in ./whoami so this stays a
 * component-only module (a clean react-refresh boundary).
 */

import { useCallback, useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { getWhoami, ApiError } from '@/lib/api';
import type { Role, Whoami } from '@/lib/types';
import { WhoamiContext, type WhoamiState } from './whoami';

const ACTIVE_ROLE_KEY_PREFIX = 'steel-cockpit:activeRole:';

/** Read the session-persisted active role for a user, tolerating a disabled store. */
function readStoredRole(email: string | null): Role | null {
  try {
    const v = sessionStorage.getItem(ACTIVE_ROLE_KEY_PREFIX + (email ?? 'anon'));
    return v === 'adjuster' || v === 'business_user' ? v : null;
  } catch {
    return null;
  }
}

/** Persist the active role for a user, tolerating a disabled store. */
function writeStoredRole(email: string | null, role: Role): void {
  try {
    sessionStorage.setItem(ACTIVE_ROLE_KEY_PREFIX + (email ?? 'anon'), role);
  } catch {
    // sessionStorage unavailable (private mode / SSR) — the switch still works in-memory.
  }
}

export function WhoamiProvider({ children }: { children: ReactNode }) {
  const [data, setData] = useState<Whoami | null>(null);
  const [loading, setLoading] = useState(true);
  const [unauthorized, setUnauthorized] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);
  const [activeRole, setActiveRoleState] = useState<Role | null>(null);

  useEffect(() => {
    const ctrl = new AbortController();
    let live = true;
    // The state resets + fetch run inside an async task (not synchronously in the
    // effect body) so a re-render isn't triggered during the effect itself.
    void (async () => {
      setLoading(true);
      setError(null);
      setUnauthorized(false);
      try {
        const w = await getWhoami(ctrl.signal);
        if (!live) return;
        setData(w);
        if (w.roles.length === 0) setUnauthorized(true);
      } catch (err: unknown) {
        if (!live || ctrl.signal.aborted) return;
        if (err instanceof ApiError && (err.status === 401 || err.status === 403)) {
          setUnauthorized(true);
        } else {
          setError(err instanceof Error ? err.message : 'Failed to load your profile');
        }
      } finally {
        if (live) setLoading(false);
      }
    })();
    return () => {
      live = false;
      ctrl.abort();
    };
  }, [nonce]);

  // Seed (or re-seed) the active persona whenever the resolved identity changes: prefer
  // a session-stored choice that is still valid for this user, else the server default.
  useEffect(() => {
    const roles = data?.roles ?? [];
    if (roles.length === 0) {
      setActiveRoleState(null);
      return;
    }
    const stored = readStoredRole(data?.email ?? null);
    const initial = stored && roles.includes(stored) ? stored : (data?.defaultRole ?? roles[0]);
    setActiveRoleState(initial);
  }, [data]);

  const setActiveRole = useCallback(
    (role: Role) => {
      const roles = data?.roles ?? [];
      // A no-op for a role the caller does not hold — the switch is a view preference,
      // never a way to assert a role you weren't granted.
      if (!roles.includes(role)) return;
      setActiveRoleState(role);
      writeStoredRole(data?.email ?? null, role);
    },
    [data]
  );

  const value: WhoamiState = {
    data,
    loading,
    unauthorized,
    error,
    reload: () => setNonce((n) => n + 1),
    roles: data?.roles ?? [],
    activeRole,
    setActiveRole,
  };
  return <WhoamiContext.Provider value={value}>{children}</WhoamiContext.Provider>;
}
