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

export interface LibraryJobBrief {
  id: string;
  type: string;
  status: string;
  progress: number;
  message: string | null;
  bytes_done: number;
  bytes_total: number | null;
  speed_bps: number | null;
  error: string | null;
}

export interface LibraryItemView {
  id: string;
  kind: "zim" | "manual";
  name: string;
  title: string;
  filename: string;
  size: number;
  sha256: string | null;
  status: string;
  error: string | null;
  job_id: string | null;
  active_job: LibraryJobBrief | null;
  file_url: string | null;
  created_at: string | null;
  installed_at: string | null;
}

export interface LibraryEntry {
  name: string;
  flavour: string | null;
  title: string;
  category: string;
  language: string;
  size: number;
  sha256: string | null;
  tier: number | null;
  status: string;
  verify_error: string | null;
  license: string | null;
  license_note: string | null;
  source_url: string | null;
  item: LibraryItemView | null;
  item_status: string | null;
  item_error: string | null;
  active_job: LibraryJobBrief | null;
  installed: boolean;
  downloadable: boolean;
}

export interface LibraryCatalog {
  updated: string | null;
  entries: LibraryEntry[];
}

export interface LibraryItems {
  items: LibraryItemView[];
  total: number;
}