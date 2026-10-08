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
  SourceRowDetail,
  SourceTarget,
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

export function getClaim(claimId: string, adjudicationId?: string | null, signal?: AbortSignal): Promise<ClaimDetail> {
  // Pass the exact adjudication_id from the selected row so the cockpit opens that precise
  // adjudication (a claim can carry several); the server falls back to RECOMMENDED-first
  // resolution when it is omitted.
  const qs = adjudicationId ? `?adjudication_id=${encodeURIComponent(adjudicationId)}` : '';
  return getJson<ClaimDetail>(`/api/claims/${encodeURIComponent(claimId)}${qs}`, signal);
}

export function getBusinessDashboardConfig(signal?: AbortSignal): Promise<BusinessDashboardConfig> {
  return getJson<BusinessDashboardConfig>('/api/business/dashboard', signal);
}

/**
 * Fetch the ENTIRE underlying row for one whitelisted evidence source (the cockpit's
 * source drill-through). The id (scalar PK or composite citation_key) and any extra key
 * values (e.g. heat_no) are URL-encoded; the server binds them and returns all columns plus
 * the fully-qualified source table name. Read-only.
 */
export function getSourceRow(target: SourceTarget, signal?: AbortSignal): Promise<SourceRowDetail> {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(target.extra ?? {})) {
    if (v !== undefined && v !== null && v !== '') p.set(k, String(v));
  }
  const qs = p.toString() ? `?${p.toString()}` : '';
  return getJson<SourceRowDetail>(
    `/api/source/${encodeURIComponent(target.source)}/${encodeURIComponent(target.id)}${qs}`,
    signal
  );
}

export async function finalizeClaim(
  claimId: string,
  adjudicationId: string,
  body: FinalizeBody
): Promise<FinalizeResult> {
  // The finalize transaction keys on adjudication_id (a claim can carry several
  // adjudications; claim_id != adjudication_id), so the exact adjudication the cockpit
  // opened is sent in the body. The claim_id stays in the path as the resource.
  const res = await fetch(`/api/claims/${encodeURIComponent(claimId)}/finalize`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify({ ...body, adjudication_id: adjudicationId }),
  });
  // The finalize endpoint returns a typed result on 200/400/404; only treat transport
  // and auth failures (401/403/5xx) as thrown errors.
  if (res.status === 401 || res.status === 403 || res.status >= 500) await readError(res);
  return (await res.json()) as FinalizeResult;
}
