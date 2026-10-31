import { z } from 'zod';
import type { components } from './generated/api';

export interface User {
  id: string;
  username: string;
  must_change_password: boolean;
}
export type Workspace = components['schemas']['Workspace'];
export type Rules = components['schemas']['Rules'];
export type Channel = components['schemas']['Channel'];
export interface Player {
  user_id: string;
  name: string;
  days: number;
  dates: string[];
  included?: boolean;
  active?: boolean;
}
export interface Snapshot {
  start: string;
  end: string;
  timezone: string;
  threshold: number;
  eligible_days: number;
  eligible_dates: string[];
  players: Player[];
  roster_size: number;
  participating: number;
  ratio: number | null;
  royalties: number;
  cutoff: string;
  roster_synced_at: string | null;
}
export interface Report {
  id: string;
  channel_id: string;
  start: string;
  end: string;
  revision: number;
  status: string;
  snapshot: Snapshot;
  created_at: string;
}
export interface Schedule {
  id: string;
  channel_id: string;
  name: string;
  frequency: 'daily' | 'weekly';
  weekdays: number[];
  wall_time: string;
  timezone: string;
  window: string;
  rolling_days: number;
  paused: boolean;
  version: number;
  next_at: string;
}
export interface Delivery {
  id: string;
  part: number;
  status: string;
  attempts: number;
  slack_ts: string | null;
  last_error: string | null;
  text: string;
}
export interface Job {
  id: number;
  kind: string;
  status: string;
  attempts: number;
  last_error: string | null;
  created_at: string;
  lease_until: string | null;
}
export interface Event {
  id: number;
  category: string;
  level: string;
  action: string;
  actor: string | null;
  target: string | null;
  workspace_id: string | null;
  details: Record<string, unknown>;
  request_id: string | null;
  created_at: string;
}
export interface Overview {
  database: string;
  worker: boolean;
  scheduler: boolean;
  workspaces: number;
  deliveries: Record<string, number>;
  jobs: Record<string, number>;
  alerts: string[];
  stale_processes: string[];
  oldest_pending: string | null;
}
export interface Envelope<T> {
  data: T;
  cursor?: number | null;
}
export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

function cookie(name: string) {
  return (
    document.cookie
      .split('; ')
      .find((item) => item.startsWith(name + '='))
      ?.split('=')
      .slice(1)
      .join('=') ?? ''
  );
}
let refreshing: Promise<void> | null = null;
async function refresh() {
  const run = async () => {
    const response = await fetch(new URL('/api/v1/auth/refresh', location.origin), {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'X-CSRF-TOKEN': decodeURIComponent(cookie('csrf_refresh_token')) },
    });
    if (!response.ok) {
      window.dispatchEvent(new Event('auth:expired'));
      throw new ApiError('Please sign in again.', 401);
    }
  };
  if (!refreshing)
    refreshing = (
      navigator.locks ? navigator.locks.request('wordle-refresh', run).then(() => undefined) : run()
    ).finally(() => {
      refreshing = null;
    });
  return refreshing;
}
export async function api<T>(
  path: string,
  options: RequestInit = {},
  allowRefresh = true,
): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body) headers.set('Content-Type', 'application/json');
  if (options.method && !['GET', 'HEAD'].includes(options.method))
    headers.set('X-CSRF-TOKEN', decodeURIComponent(cookie('csrf_access_token')));
  let response = await fetch(new URL('/api/v1' + path, location.origin), {
    ...options,
    headers,
    credentials: 'same-origin',
  });
  if (response.status === 401 && allowRefresh && !['/auth/login', '/auth/refresh'].includes(path)) {
    await refresh();
    headers.set('X-CSRF-TOKEN', decodeURIComponent(cookie('csrf_access_token')));
    response = await fetch(new URL('/api/v1' + path, location.origin), {
      ...options,
      headers,
      credentials: 'same-origin',
    });
  }
  const value = await response.json().catch(() => ({}));
  if (!response.ok) {
    const parsed = z
      .object({
        error: z.string().optional(),
        msg: z.string().optional(),
        message: z.string().optional(),
      })
      .safeParse(value);
    throw new ApiError(
      parsed.success
        ? parsed.data.error || parsed.data.msg || parsed.data.message || 'Request failed'
        : 'Request failed',
      response.status,
    );
  }
  return value as T;
}
export function write<T>(path: string, body: unknown, method = 'POST', headers?: HeadersInit) {
  return api<Envelope<T>>(path, { method, body: JSON.stringify(body), headers });
}
