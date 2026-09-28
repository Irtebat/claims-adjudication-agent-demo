/**
 * Signed-in user + resolved role state, shared via context. The role is resolved
 * SERVER-SIDE (SCIM group membership) and only mirrored here to shape navigation —
 * every backend route independently re-enforces authorization, so the client role is a
 * convenience, never the control. Kept in its own hook-only module (no component
 * export) so the provider file stays a clean react-refresh boundary.
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
}

export const WhoamiContext = createContext<WhoamiState | null>(null);

export function useWhoami(): WhoamiState {
  const ctx = useContext(WhoamiContext);
  if (!ctx) throw new Error('useWhoami must be used within a WhoamiProvider');
  return ctx;
}

export function useRole(): Role | null {
  return useWhoami().data?.role ?? null;
}
