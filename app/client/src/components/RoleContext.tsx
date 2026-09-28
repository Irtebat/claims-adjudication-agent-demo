/**
 * Signed-in user + resolved role, fetched once from `/api/whoami`. The role is resolved
 * SERVER-SIDE (SCIM group membership) and only mirrored here to shape the navigation —
 * every backend route independently re-enforces authorization, so the client role is a
 * convenience, never the control. A 403 means the caller matched no role and is treated
 * as unauthorized.
 */

import { createContext, useContext, useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { getWhoami } from '@/lib/api';
import { ApiError } from '@/lib/api';
import type { Role, Whoami } from '@/lib/types';

interface WhoamiState {
  data: Whoami | null;
  loading: boolean;
  /** True when the user resolved to no role (403 / unauthorized). */
  unauthorized: boolean;
  error: string | null;
  reload: () => void;
}

const WhoamiContext = createContext<WhoamiState | null>(null);

export function WhoamiProvider({ children }: { children: ReactNode }) {
  const [data, setData] = useState<Whoami | null>(null);
  const [loading, setLoading] = useState(true);
  const [unauthorized, setUnauthorized] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);

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
        if (!w.role) setUnauthorized(true);
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

  const value: WhoamiState = {
    data,
    loading,
    unauthorized,
    error,
    reload: () => setNonce((n) => n + 1),
  };
  return <WhoamiContext.Provider value={value}>{children}</WhoamiContext.Provider>;
}

export function useWhoami(): WhoamiState {
  const ctx = useContext(WhoamiContext);
  if (!ctx) throw new Error('useWhoami must be used within a WhoamiProvider');
  return ctx;
}

export function useRole(): Role | null {
  return useWhoami().data?.role ?? null;
}
