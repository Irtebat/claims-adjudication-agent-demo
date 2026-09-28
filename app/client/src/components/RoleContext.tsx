/**
 * WhoamiProvider — fetches the signed-in user + resolved role once from `/api/whoami`
 * and exposes it via WhoamiContext. The role is resolved SERVER-SIDE (SCIM group
 * membership) and only mirrored here to shape navigation; every backend route
 * independently re-enforces authorization, so the client role is a convenience, never
 * the control. A 403 means the caller matched no role and is treated as unauthorized.
 * The hooks (useWhoami / useRole) live in ./whoami so this stays a component-only
 * module (a clean react-refresh boundary).
 */

import { useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { getWhoami, ApiError } from '@/lib/api';
import type { Whoami } from '@/lib/types';
import { WhoamiContext, type WhoamiState } from './whoami';

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
