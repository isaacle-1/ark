export class ApiError extends Error {
  readonly status: number;
  readonly requestId?: string;

  constructor(status: number, message: string, requestId?: string) {
    super(message);
    this.status = status;
    this.requestId = requestId;
  }
}

function describeDetail(detail: unknown, fallback: string): string {
  if (Array.isArray(detail)) {
    return detail.map((d) => (d && typeof d === "object" ? (d as any).msg : String(d))).join("; ");
  }
  if (detail && typeof detail === "object") return JSON.stringify(detail);
  return detail ? String(detail) : fallback;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const isForm = init?.body instanceof FormData;
  const res = await fetch(path, {
    credentials: "same-origin",
    headers: {
      ...(isForm ? {} : { "Content-Type": "application/json" }),
      ...(init?.headers ?? {}),
    },
    ...init,
  });
  if (res.ok) {
    if (res.status === 204) return undefined as T;
    return (await res.json()) as T;
  }
  let detail: unknown = res.statusText;
  let requestId = res.headers.get("X-Request-ID") ?? undefined;
  try {
    const body = await res.json();
    if (body && typeof body === "object") {
      const b = body as Record<string, unknown>;
      if (b.detail !== undefined) detail = b.detail;
      if (typeof b.request_id === "string") requestId = b.request_id;
    }
  } catch {
    /* non-JSON error body — fall back to defaults */
  }
  throw new ApiError(res.status, describeDetail(detail, res.statusText), requestId);
}

export const api = {
  get: <T>(path: string): Promise<T> => request<T>(path),
  post: <T>(path: string, body?: unknown): Promise<T> =>
    request<T>(path, { method: "POST", body: JSON.stringify(body ?? {}) }),
  put: <T>(path: string, body?: unknown): Promise<T> =>
    request<T>(path, { method: "PUT", body: JSON.stringify(body ?? {}) }),
  del: <T>(path: string): Promise<T> => request<T>(path, { method: "DELETE" }),
  postFile: <T>(path: string, file: File): Promise<T> => {
    const form = new FormData();
    form.append("file", file, file.name);
    return request<T>(path, { method: "POST", body: form });
  },
};

export function fmtBytes(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  if (n < 1024) return `${n} B`;
  const units = ["KiB", "MiB", "GiB", "TiB"];
  let v = n;
  let u = -1;
  do {
    v /= 1024;
    u += 1;
  } while (v >= 1024 && u < units.length - 1);
  return `${v.toFixed(1)} ${units[u]}`;
}

export function fmtDuration(seconds: number | null | undefined): string {
  if (seconds == null) return "—";
  const s = Math.max(0, Math.round(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${sec}s`;
  return `${sec}s`;
}