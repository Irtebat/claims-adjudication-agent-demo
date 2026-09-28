/**
 * Signed-in user + resolved role state, shared via context. Roles are resolved
 * SERVER-SIDE (allowlist / SCIM group membership) and only mirrored here to shape
 * navigation — every backend route independently re-enforces authorization, so the
 * client role is a convenience, never the control.
 *
 * A caller may hold BOTH roles. `roles` is the full server-resolved set; `activeRole`
 * is the persona the UI is currently presenting (a VIEW preference, persisted for the
 * session, seeded from the server's `defaultRole`). `setActiveRole` only accepts a role
 * the user actually holds — it changes the view, not the user's permissions. Kept in
 * its own hook-only module (no component export) so the provider file stays a clean
 * react-refresh boundary.
 */

import { createContext, useContext } from 'react';
import type { Role, Whoami } from '@/lib/types';

export interface WhoamiState {
  data: Whoami | null;
  loading: boolean;
  /** True when the user resolved to no role (403 / unauthorized). */
  unauthorized: boolean;
  error: string | null;
  reload: () => void;
  /** The full set of roles the server resolved for the caller (authoritative). */
  roles: Role[];
  /** The persona the UI is currently presenting (view preference), or null pre-load. */
  activeRole: Role | null;
  /** Switch the active persona. Ignored if `role` is not one the caller holds. */
  setActiveRole: (role: Role) => void;
}

export const WhoamiContext = createContext<WhoamiState | null>(null);

export function useWhoami(): WhoamiState {
  const ctx = useContext(WhoamiContext);
  if (!ctx) throw new Error('useWhoami must be used within a WhoamiProvider');
  return ctx;
}

/** The full set of roles the caller holds (server-resolved). */
export function useRoles(): Role[] {
  return useWhoami().roles;
}

/**
 * The role the UI is currently acting as. Navigation, home redirect, route gating, and
 * role-specific affordances key off this — but the SERVER remains authoritative, so a
 * view keyed off the active role can never exceed what the caller's `roles` permit.
 */
export function useActiveRole(): Role | null {
  return useWhoami().activeRole;
}

/** Back-compat alias: the role the UI is currently presenting (the active persona). */
export function useRole(): Role | null {
  return useActiveRole();
}

/** Setter for the active persona (accepts only a role the caller holds). */
export function useSetActiveRole(): (role: Role) => void {
  return useWhoami().setActiveRole;
}
