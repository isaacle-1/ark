export interface Health {
  status: "ok" | "degraded";
  version: string;
  ark_home: string;
  pid: number;
  started_at: string;
  uptime_seconds: number;
  problems: string[];
  auth_mode: "open" | "required";
  log_level: string;
  modules: Array<{
    name: string;
    healthy: boolean;
    status: string;
    reason: string | null;
    queued?: number;
    running?: number;
  }>;
  sidecars: Array<Record<string, unknown>>;
  disk: {
    free_bytes: number;
    total_bytes: number;
    used_bytes: number;
    free_pct: number;
    data_subdirs: Array<{ name: string; size_bytes: number }>;
  };
  system: {
    cpu_percent?: number;
    memory_percent?: number;
    memory_used_bytes?: number;
    memory_total_bytes?: number;
    error?: string;
  };
  db: { ok: boolean; integrity: string; journal_mode: string | null; size_bytes: number | null };
}

export interface UserInfo {
  id?: number;
  username: string;
  role: "admin" | "user";
}

export interface Me {
  user: UserInfo | null;
  mode: "open" | "required";
}

export interface LogEntry {
  ts: string;
  level: string;
  logger: string;
  msg: string;
  request_id?: string;
  job_id?: string;
  src?: string;
  [key: string]: unknown;
}

export interface LogHistory {
  items: LogEntry[];
  file: string;
  total_in_file: number;
}

export interface LogFile {
  name: string;
  size_bytes: number;
  mtime: number;
}

export interface Job {
  id: string;
  type: string;
  status: string;
  progress: number;
  message: string | null;
  bytes_done: number;
  bytes_total: number | null;
  speed_bps: number | null;
  error: string | null;
  payload: Record<string, unknown>;
  result: unknown;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
}