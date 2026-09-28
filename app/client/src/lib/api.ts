/**
 * Typed client for the Stage-A JSON backend. Every call returns parsed data or throws
 * an `ApiError` carrying the HTTP status and the server's `error` code, so screens can
 * distinguish 403 (role) / 404 (missing) / 400 (validation) from a generic failure and
 * render the right state. All reads are GET; finalize is the one POST.
 */

import type {
  BusinessDashboardConfig,
  ClaimDetail,
  FinalizeBody,
  FinalizeResult,
  HistoryItem,
  ListFilters,
  QueueItem,
  Whoami,
} from './types';

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  constructor(status: number, code: string, message?: string) {
    super(message ?? code);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
  }
}

async function readError(res: Response): Promise<never> {
  let code = `http_${res.status}`;
  try {
    const body: unknown = await res.json();
    if (body && typeof body === 'object' && 'error' in body && typeof body.error === 'string') {
      code = body.error;
    }
  } catch {
    // Non-JSON error body; keep the http_<status> code.
  }
  throw new ApiError(res.status, code);
}

async function getJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(url, { headers: { Accept: 'application/json' }, signal });
  if (!res.ok) await readError(res);
  return (await res.json()) as T;
}

function query(filters: ListFilters = {}): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(filters)) {
    if (v !== undefined && v !== null && v !== '') p.set(k, String(v));
  }
  const s = p.toString();
  return s ? `?${s}` : '';
}

export function getWhoami(signal?: AbortSignal): Promise<Whoami> {
  return getJson<Whoami>('/api/whoami', signal);
}

export async function getQueue(filters: ListFilters = {}, signal?: AbortSignal): Promise<QueueItem[]> {
  const data = await getJson<{ items: QueueItem[]; count: number }>(`/api/queue${query(filters)}`, signal);
  return data.items;
}

export async function getHistory(filters: ListFilters = {}, signal?: AbortSignal): Promise<HistoryItem[]> {
  const data = await getJson<{ items: HistoryItem[]; count: number }>(`/api/history${query(filters)}`, signal);
  return data.items;
}

export function getClaim(claimId: string, signal?: AbortSignal): Promise<ClaimDetail> {
  return getJson<ClaimDetail>(`/api/claims/${encodeURIComponent(claimId)}`, signal);
}

export function getBusinessDashboardConfig(signal?: AbortSignal): Promise<BusinessDashboardConfig> {
  return getJson<BusinessDashboardConfig>('/api/business/dashboard', signal);
}

export async function finalizeClaim(claimId: string, body: FinalizeBody): Promise<FinalizeResult> {
  const res = await fetch(`/api/claims/${encodeURIComponent(claimId)}/finalize`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify(body),
  });
  // The finalize endpoint returns a typed result on 200/400/404; only treat transport
  // and auth failures (401/403/5xx) as thrown errors.
  if (res.status === 401 || res.status === 403 || res.status >= 500) await readError(res);
  return (await res.json()) as FinalizeResult;
}
